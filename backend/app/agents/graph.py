"""LangGraph agent.

START -> router -> {knowledge | tools | confirm | escalate | unknown} -> validator -> END
                                                      validator -> escalate -> validator (once)

Each node has one responsibility. The router decides; it never answers. `trace` entries are
short, human-readable summaries of what the system did (not model reasoning).
"""
import hashlib
import json
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import render
from app.agents.deps import AgentDeps
from app.agents.router import (
    INTENTS,
    NO_RE,
    YES_RE,
    classify_rules,
    extract_order_ids,
    looks_like_knowledge,
    wants_personal_refund_check,
)
from app.agents.validator import validate_knowledge, validate_tool
from app.rag.embeddings import EmbeddingError
from app.rag.pipeline import FALLBACK_MESSAGE, RAGResult
from app.rag.retriever import RetrievedChunk
from app.rag.text import terms
from app.services.cache import kb_version
from app.services.llm.base import LLMError

CLASSIFY_PROMPT = (
    "Classify the customer message into exactly one intent.\n"
    "Intents: knowledge_question (policies, how-to, general information), order_status, "
    "customer_information, refund_question, cancellation_request, complaint, "
    "escalation (wants a human), unknown (greeting or unrelated).\n"
    'Reply with JSON only: {"intent": "<name>"}'
)
NEEDS_AUTH_MSG = "To look up your orders or account I need to know who you are. Please sign in first, then ask again."


class AgentState(TypedDict, total=False):
    message: str
    history: list[dict]
    memory: dict
    intent: str
    route: str
    decision: str  # yes | no (for confirmations)
    answer: str
    citations: list[dict]
    status: str
    escalated: bool
    escalate_now: bool
    escalation_reason: dict
    evidence: dict
    validation: dict
    trace: Annotated[list[dict], operator.add]


def step(name: str, detail: str) -> dict:
    return {"step": name, "detail": detail}


def _offer_human(mem: dict, reason: str, category: str = "general", order_id: str | None = None) -> None:
    mem["pending_action"] = {"type": "escalate_offer", "reason": reason, "category": category, "order_id": order_id}


class SupportGraph:
    def __init__(self, deps: AgentDeps):
        self.d = deps

    # ---------------------------------------------------------------- router
    def router_node(self, state: AgentState) -> dict:
        msg = state["message"]
        mem = dict(state["memory"])
        trace: list[dict] = []
        pending = mem.get("pending_action")

        if pending:
            if YES_RE.match(msg):
                mem["pending_action"] = None
                if pending["type"] == "escalate_offer":
                    trace.append(step("router", "Customer accepted the human hand-off"))
                    return {"memory": mem, "intent": "escalation", "route": "escalate", "trace": trace,
                            "escalation_reason": {k: pending.get(k) for k in ("reason", "category", "order_id")}}
                trace.append(step("router", f"Customer confirmed pending action: {pending['type']}"))
                return {"memory": {**mem, "pending_action": pending}, "intent": "cancellation_request",
                        "route": "confirm", "decision": "yes", "trace": trace}
            if NO_RE.match(msg):
                mem["pending_action"] = None
                trace.append(step("router", f"Customer declined pending action: {pending['type']}"))
                return {"memory": mem, "intent": "cancellation_request" if pending["type"] == "cancel_order" else "unknown",
                        "route": "confirm", "decision": "no", "trace": trace}
            mem["pending_action"] = None
            trace.append(step("router", "Pending action discarded: customer moved on to a new request"))

        ids = extract_order_ids(msg)
        if ids:
            mem["last_order_id"] = ids[0]
            trace.append(step("memory", f"Order {ids[0]} noted for this conversation"))

        # Follow-up to "which order?" with just an order number.
        awaiting = mem.get("awaiting_order_for")
        if awaiting and ids and len(msg.split()) <= 6:
            intent, reason = awaiting, "order number supplied after a clarification question"
        else:
            hit = classify_rules(msg, bool(mem.get("last_order_id")))
            if hit:
                intent, reason = hit
            else:
                intent, reason = self._llm_classify(msg), "LLM classification (rules inconclusive)"
        mem["awaiting_order_for"] = None

        has_order = bool(ids or mem.get("last_order_id"))
        if intent == "refund_question":
            route = "tools" if wants_personal_refund_check(msg, has_order) else "knowledge"
        else:
            route = {
                "knowledge_question": "knowledge", "order_status": "tools", "customer_information": "tools",
                "cancellation_request": "tools", "complaint": "escalate", "escalation": "escalate",
                "unknown": "unknown",
            }[intent]
        trace.append(step("router", f"Intent: {intent} ({reason}); route: {route}"))
        out: dict = {"memory": mem, "intent": intent, "route": route, "trace": trace}
        if route == "escalate":
            category = "payment" if "payment" in reason else "complaint" if intent == "complaint" else "general"
            out["escalation_reason"] = {
                "reason": {"complaint": "Customer complaint", "escalation": reason}.get(intent, reason).capitalize(),
                "category": category,
                "priority": "high" if category in {"payment", "complaint"} else "normal",
                "order_id": ids[0] if ids else None,
            }
        return out

    def _llm_classify(self, msg: str) -> str:
        try:
            res = self.d.llm.chat(
                [{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": msg[:500]}],
                temperature=0.0, max_tokens=30, json_mode=True,
            )
            self.d.metrics.add_llm(res)
            intent = json.loads(res.text).get("intent", "unknown")
        except (LLMError, ValueError, AttributeError):
            intent = "unknown"
        if intent not in INTENTS:
            intent = "unknown"
        if intent == "unknown" and looks_like_knowledge(msg):
            intent = "knowledge_question"
        return intent

    # ------------------------------------------------------------- knowledge
    def knowledge_node(self, state: AgentState) -> dict:
        d, msg = self.d, state["message"]
        query = msg
        prior = [m["content"] for m in state.get("history", []) if m["role"] == "user"]
        if len(terms(msg)) <= 2 and prior:  # short follow-up: borrow the previous question's topic
            query = f"{prior[-1]} {msg}"
        key = f"kbq:{kb_version(d.cache)}:{hashlib.sha1(' '.join(terms(query)).encode()).hexdigest()}"
        cached = d.cache.get(key)
        if cached:
            d.metrics.cache_hit = True
            d.metrics.retrieved_count = len(cached["chunks"])
            d.metrics.top_score = cached.get("top_score")
            return {
                "answer": cached["answer"], "citations": cached["citations"],
                "evidence": {"kind": "rag", "chunks": cached["chunks"], "top_score": cached.get("top_score"),
                             "from_cache": True, "cache_key": key},
                "trace": [step("cache", "Served a previously validated answer from cache")],
            }
        try:
            retrieval = d.rag.retrieve(query)  # embeds the query, so it also needs the model server
        except EmbeddingError as exc:
            d.events.emit("embedding_unavailable", d.conversation_id, error=str(exc)[:200])
            return {"answer": "", "citations": [], "trace": [step("retrieval", "Embedding model unavailable")],
                    "evidence": {"kind": "rag", "chunks": [], "top_score": None, "error": str(exc), "from_cache": False}}
        d.metrics.retrieval_ms += retrieval.latency_ms
        d.metrics.retrieved_count = len(retrieval.relevant)
        d.metrics.top_score = retrieval.top_score
        d.events.emit("retrieval", d.conversation_id, duration_ms=round(retrieval.latency_ms, 1),
                      candidates=len(retrieval.ranked), relevant=len(retrieval.relevant), top_score=retrieval.top_score)
        trace = [step("retrieval", f"{len(retrieval.relevant)} of {len(retrieval.ranked)} candidate chunks passed the "
                                   f"relevance threshold (top score {retrieval.top_score})")]
        evidence = {"kind": "rag", "chunks": [], "top_score": retrieval.top_score, "cache_key": key, "from_cache": False}
        try:
            result: RAGResult = d.rag.answer(msg, state.get("history"), retrieval)
        except LLMError as exc:
            evidence["error"] = str(exc)
            trace.append(step("llm", "Language model unavailable"))
            return {"answer": "", "citations": [], "evidence": evidence, "trace": trace}
        d.metrics.add_llm(result.llm)
        if result.llm:
            d.events.emit("llm_call", d.conversation_id, duration_ms=round(result.llm.latency_ms, 1), model=result.llm.model,
                          prompt_tokens=result.llm.prompt_tokens, completion_tokens=result.llm.completion_tokens)
            trace.append(step("llm", f"Generated a grounded answer from {len(result.chunks)} context chunk(s)"))
        evidence["chunks"] = [c.to_dict() for c in result.chunks]
        evidence["citation_mode"] = result.citation_mode
        return {"answer": result.answer, "citations": result.citations, "evidence": evidence, "trace": trace}

    # ----------------------------------------------------------------- tools
    def tools_node(self, state: AgentState) -> dict:
        d, msg, intent = self.d, state["message"], state["intent"]
        mem = dict(state["memory"])
        ids = extract_order_ids(msg)
        order_id = ids[0] if ids else mem.get("last_order_id")
        base = {"memory": mem, "evidence": {"kind": "tool"}, "citations": []}

        if not d.customer_id:
            return {**base, "answer": NEEDS_AUTH_MSG, "status": "clarification",
                    "trace": [step("permission", "No signed-in customer; tools not executed")]}

        if intent == "customer_information":
            r = d.call_tool("get_customer_details", {"customer_id": d.customer_id})
            answer = render.render_customer(r.data) if r.success else r.error
            return {**base, "answer": answer, "status": "answered" if r.success else "error",
                    "trace": [step("tool", f"get_customer_details -> {'ok' if r.success else r.error_code}")]}

        if not order_id:
            mem["awaiting_order_for"] = intent
            return {**base, "answer": "Which order is this about? Please share the order number (it looks like ORD-1234).",
                    "status": "clarification", "trace": [step("clarify", "Order number needed")]}

        if intent == "order_status":
            return self._order_status(base, order_id)
        if intent == "refund_question":
            return self._refund(base, order_id)
        return self._cancellation(base, mem, order_id)

    def _order_status(self, base: dict, order_id: str) -> dict:
        d = self.d
        r = d.call_tool("get_order_status", {"order_id": order_id})
        trace = [step("tool", f"get_order_status({order_id}) -> {'ok' if r.success else r.error_code}")]
        if not r.success:
            return {**base, "answer": r.error, "status": "answered", "trace": trace}
        items = d.call_tool("get_order_items", {"order_id": order_id})
        trace.append(step("tool", f"get_order_items({order_id}) -> {'ok' if items.success else items.error_code}"))
        return {**base, "answer": render.render_order_status(r.data, items.data if items.success else None),
                "status": "answered", "trace": trace}

    def _refund(self, base: dict, order_id: str) -> dict:
        d = self.d
        r = d.call_tool("check_refund_eligibility", {"order_id": order_id})
        trace = [step("tool", f"check_refund_eligibility({order_id}) -> {'ok' if r.success else r.error_code}")]
        if not r.success:
            return {**base, "answer": r.error, "status": "answered", "trace": trace}
        answer = render.render_refund_eligibility(r.data)
        # Attach the policy passage that defines the rule (retrieval only, no generation).
        retrieval = d.rag.retrieve("refund return policy eligibility window")
        d.metrics.retrieval_ms += retrieval.latency_ms
        citations = []
        if retrieval.relevant:
            from app.rag.pipeline import citations_from_answer

            citations, _ = citations_from_answer("[1]", retrieval.relevant[:1])
            trace.append(step("retrieval", "Attached the refund policy passage as a source"))
        mem = dict(base["memory"])
        if r.data["eligible"]:
            answer += "\nWould you like me to open a support ticket so an agent can arrange the return?"
            _offer_human(mem, f"Return request for {order_id}", "refund", order_id)
        return {**base, "memory": mem, "answer": answer, "citations": citations, "status": "answered", "trace": trace}

    def _cancellation(self, base: dict, mem: dict, order_id: str) -> dict:
        d = self.d
        r = d.call_tool("get_order_status", {"order_id": order_id})
        trace = [step("tool", f"get_order_status({order_id}) -> {'ok' if r.success else r.error_code}")]
        if not r.success:
            return {**base, "answer": r.error, "status": "answered", "trace": trace}
        o = r.data
        if o["status"] == "cancelled":
            return {**base, "answer": f"Order {order_id} is already cancelled.", "status": "answered", "trace": trace}
        if not o["cancellable"]:
            _offer_human(mem, f"Customer wants to cancel {order_id} but it is {o['status']}", "order", order_id)
            msg = f"Order {order_id} is {o['status']}, so it can no longer be cancelled."
            if o["status"] == "delivered":
                msg += " You may be able to return it instead."
            return {**base, "memory": mem, "status": "answered", "trace": trace,
                    "answer": msg + " Would you like me to connect you with a human support agent?"}
        items = d.call_tool("get_order_items", {"order_id": order_id})
        names = ", ".join(i["name"] for i in items.data["items"]) if items.success else "your items"
        mem["pending_action"] = {"type": "cancel_order", "order_id": order_id}
        trace.append(step("confirmation", f"Waiting for the customer to confirm cancelling {order_id}"))
        return {**base, "memory": mem, "status": "needs_confirmation", "trace": trace,
                "answer": (f"I found order {order_id} ({o['status']}, {render.money(o['total'], o['currency'])}): {names}.\n"
                           "Cancelling cannot be undone. Would you like me to proceed with the cancellation? "
                           "Reply 'yes' to confirm or 'no' to keep the order.")}

    # --------------------------------------------------------------- confirm
    def confirm_node(self, state: AgentState) -> dict:
        d, mem = self.d, dict(state["memory"])
        pending = mem.pop("pending_action", None)
        mem["pending_action"] = None
        base = {"memory": mem, "evidence": {"kind": "tool"}, "citations": []}
        if state.get("decision") == "no" or not pending:
            return {**base, "answer": "No problem, I've left everything as it is. Is there anything else I can help with?",
                    "status": "answered", "trace": [step("confirmation", "Action declined; nothing changed")]}
        order_id = pending["order_id"]
        r = d.call_tool("cancel_order", {"order_id": order_id}, confirmed=True)
        trace = [step("tool", f"cancel_order({order_id}) [confirmed] -> {'ok' if r.success else r.error_code}")]
        if r.success:
            answer = (f"Order {order_id} has been cancelled. A refund of {render.money(r.data['refund_due'])} "
                      "has been initiated to your original payment method.")
            return {**base, "answer": answer, "status": "completed", "trace": trace}
        return {**base, "answer": r.error, "status": "answered", "trace": trace}

    # ---------------------------------------------------------------- escalate
    def escalate_node(self, state: AgentState) -> dict:
        d, mem = self.d, dict(state["memory"])
        info = state.get("escalation_reason") or {"reason": "Customer needs human help", "category": "general"}
        base = {"memory": mem, "evidence": {"kind": None}, "citations": [], "escalate_now": False}
        if mem.get("ticket_ref"):
            return {**base, "answer": f"This conversation is already with our human support team (ticket {mem['ticket_ref']}).",
                    "status": "escalated", "escalated": True, "trace": [step("escalation", "Already escalated")]}
        user_msgs = [m["content"] for m in state.get("history", []) if m["role"] == "user"][-3:] + [state["message"]]
        description = "Reason: {}\nRecent customer messages:\n{}".format(
            info.get("reason"), "\n".join(f"- {m[:300]}" for m in user_msgs))
        args = {"subject": (info.get("reason") or "Customer needs help")[:120], "description": description,
                "category": info.get("category") or "general", "priority": info.get("priority") or "normal"}
        order_id = info.get("order_id")
        r = d.call_tool("create_support_ticket", {**args, "order_id": order_id} if order_id and d.customer_id else args)
        if not r.success and order_id:
            r = d.call_tool("create_support_ticket", args)
        if not r.success:
            return {**base, "answer": "I couldn't create a support ticket just now. Please try again in a moment.",
                    "status": "error", "trace": [step("escalation", f"Ticket creation failed: {r.error_code}")]}
        ref = r.data["reference"]
        mem["ticket_ref"] = ref
        answer = f"I've passed this to our human support team (ticket {ref}). An agent will follow up, and they can see this conversation, so you won't need to repeat yourself."
        if info.get("category") == "payment":
            answer += " If you have a transaction ID or the last four digits of the card used, add it to this chat."
        if state["intent"] == "complaint":
            answer = "I'm sorry about this experience. " + answer
        return {**base, "memory": mem, "answer": answer, "status": "escalated", "escalated": True,
                "trace": [step("escalation", f"Created ticket {ref} ({args['category']}, {args['priority']} priority)")]}

    # ----------------------------------------------------------------- unknown
    def unknown_node(self, state: AgentState) -> dict:
        mem = dict(state["memory"])
        mem["unknown_count"] = mem.get("unknown_count", 0) + 1
        answer = render.UNKNOWN_MENU
        status = "clarification"
        if mem["unknown_count"] >= 3:
            _offer_human(mem, "Assistant could not understand the request after several attempts")
            answer = "I'm having trouble understanding the request. Would you like me to connect you with a human support agent?"
        return {"memory": mem, "answer": answer, "status": status, "citations": [], "evidence": {"kind": None},
                "trace": [step("unknown", "Intent unclear; offered help options")]}

    # ---------------------------------------------------------------- validator
    def validator_node(self, state: AgentState) -> dict:
        d, ev = self.d, state.get("evidence") or {}
        mem, answer, status = dict(state["memory"]), state.get("answer", ""), state.get("status", "answered")
        citations, escalate_now = state.get("citations", []), False
        kind = ev.get("kind")

        if kind == "rag":
            chunks = [RetrievedChunk(**c) for c in ev.get("chunks", [])]
            v = validate_knowledge(answer, chunks, citations, ev.get("top_score"), d.settings.min_grounding)
            if v.passed:
                mem["fallback_count"], status = 0, "answered"
                if not ev.get("from_cache") and ev.get("cache_key"):
                    d.cache.set(ev["cache_key"], {"answer": answer, "citations": citations, "chunks": ev["chunks"],
                                                  "top_score": ev.get("top_score")}, d.settings.cache_ttl_seconds)
            else:
                mem["fallback_count"] = mem.get("fallback_count", 0) + 1
                citations = []
                if ev.get("error"):
                    answer, status = "I'm having trouble reaching the language model right now.", "error"
                else:
                    answer, status = FALLBACK_MESSAGE, "fallback"
                if mem["fallback_count"] >= 2 and not state.get("escalated"):
                    escalate_now = True
                    answer += " Since this is the second time, I'll bring in a human support agent."
                    state_reason = {"reason": "Assistant could not answer from the knowledge base", "category": "general"}
                else:
                    answer += " Would you like me to connect you with a human support agent?"
                    _offer_human(mem, "Question could not be answered from the knowledge base")
                    state_reason = None
                d.events.emit("validation_failed", d.conversation_id, reasons=v.reasons)
                return {"answer": answer, "citations": citations, "status": status, "memory": mem,
                        "validation": v.to_dict(), "escalate_now": escalate_now,
                        **({"escalation_reason": state_reason} if state_reason else {}),
                        "trace": [step("validation", "Failed: " + "; ".join(v.reasons or ["unsupported answer"]))]}
            trace = [step("validation", "Passed: evidence retrieved, answer grounded, citation present")]
        elif kind == "tool":
            v = validate_tool([r.summary() for r in d.tool_log], answer)
            if not v.passed:
                answer, status = "Something went wrong while looking that up. Would you like me to connect you with a human support agent?", "error"
                _offer_human(mem, "A backend tool failed unexpectedly")
            trace = [step("validation", "Passed: tool returned valid data" if v.passed else "Failed: " + "; ".join(v.reasons))]
        else:
            from app.agents.validator import ValidationResult

            v = ValidationResult(bool(answer.strip()), {"answer_present": bool(answer.strip())})
            trace = []
        return {"answer": answer, "citations": citations, "status": status, "memory": mem,
                "validation": v.to_dict(), "escalate_now": False, "trace": trace}

    # ------------------------------------------------------------------ build
    def build(self):
        g = StateGraph(AgentState)
        g.add_node("router_node", self.router_node)
        g.add_node("knowledge_node", self.knowledge_node)
        g.add_node("tools_node", self.tools_node)
        g.add_node("confirm_node", self.confirm_node)
        g.add_node("escalate_node", self.escalate_node)
        g.add_node("unknown_node", self.unknown_node)
        g.add_node("validator_node", self.validator_node)
        g.add_edge(START, "router_node")
        g.add_conditional_edges("router_node", lambda s: s["route"], {
            "knowledge": "knowledge_node", "tools": "tools_node", "confirm": "confirm_node",
            "escalate": "escalate_node", "unknown": "unknown_node"})
        for n in ("knowledge_node", "tools_node", "confirm_node", "escalate_node", "unknown_node"):
            g.add_edge(n, "validator_node")
        g.add_conditional_edges(
            "validator_node",
            lambda s: "escalate" if s.get("escalate_now") and not s.get("escalated") else "end",
            {"escalate": "escalate_node", "end": END},
        )
        return g.compile()

