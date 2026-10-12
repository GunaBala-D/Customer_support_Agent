"""One chat turn: load conversation -> run the LangGraph agent -> persist -> emit events."""
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.deps import AgentDeps, new_memory
from app.agents.graph import SupportGraph
from app.models.db import Conversation, Message
from app.services.container import Container


class ConversationAccessError(Exception):
    pass


def handle_chat(c: Container, session: Session, *, message: str, conversation_id: str | None, customer_id: str | None) -> dict:
    start = time.perf_counter()
    conv = session.get(Conversation, conversation_id) if conversation_id else None
    if conversation_id and conv is None:
        conv = Conversation(id=conversation_id, customer_id=customer_id, title=message[:80], state=new_memory())
        session.add(conv)
    elif conv is None:
        conv = Conversation(id=str(uuid.uuid4()), customer_id=customer_id, title=message[:80], state=new_memory())
        session.add(conv)
    elif conv.customer_id != customer_id:
        raise ConversationAccessError("This conversation belongs to a different customer.")
    session.flush()

    recent = session.scalars(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.id.desc()).limit(6)
    ).all()
    history = [{"role": "assistant" if m.role == "agent" else m.role, "content": m.content} for m in reversed(recent)]
    user_msg = Message(conversation_id=conv.id, role="user", content=message)
    session.add(user_msg)
    session.commit()

    deps = AgentDeps(session, c.settings, c.llm, c.rag, c.cache, c.events, conv.id, customer_id)
    memory = {**new_memory(), **(conv.state or {})}
    error = None
    try:
        final = SupportGraph(deps).build().invoke(
            {"message": message, "history": history, "memory": memory, "trace": [], "citations": []}
        )
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to a customer
        session.rollback()
        c.events.emit("error", conv.id, error=type(exc).__name__, detail=str(exc)[:300])
        error = type(exc).__name__
        final = {"answer": "Sorry, something went wrong on our side. Please try again, or ask for a human agent.",
                 "status": "error", "intent": "unknown", "memory": memory, "citations": [], "trace": []}

    status = final.get("status", "answered")
    latency_ms = (time.perf_counter() - start) * 1000
    m = deps.metrics
    assistant = Message(
        conversation_id=conv.id, role="assistant", content=final["answer"], intent=final.get("intent"),
        response_status=status, citations=final.get("citations") or None, trace=final.get("trace"),
        tool_calls=[t.summary() for t in deps.tool_log] or None,
        model=m.model or c.llm.model, latency_ms=latency_ms, llm_ms=m.llm_ms, retrieval_ms=m.retrieval_ms,
        tool_ms=m.tool_ms, retrieved_count=m.retrieved_count, top_score=m.top_score,
        prompt_tokens=m.prompt_tokens or None, completion_tokens=m.completion_tokens or None,
    )
    session.add(assistant)
    user_msg.intent = final.get("intent")
    conv = session.get(Conversation, conv.id)
    conv.state = dict(final["memory"])  # new dict object so the JSON column change is detected
    if final.get("escalated") or conv.status == "escalated":
        conv.status = "escalated"
    else:
        # "resolved" = the last turn was answered/completed without escalation (a heuristic, see README)
        conv.status = "resolved" if status in {"answered", "completed"} else "open"
    session.commit()

    c.events.emit(
        "chat_response", conv.id, intent=final.get("intent"), status=status, latency_ms=round(latency_ms, 1),
        llm_ms=round(m.llm_ms, 1), retrieval_ms=round(m.retrieval_ms, 1), tool_ms=round(m.tool_ms, 1),
        retrieved_chunks=m.retrieved_count, tools=[t.tool for t in deps.tool_log], model=m.model or c.llm.model,
        prompt_tokens=m.prompt_tokens or None, completion_tokens=m.completion_tokens or None,
        cache_hit=m.cache_hit, error=error,
    )
    return {
        "conversation_id": conv.id,
        "message_id": assistant.id,
        "answer": final["answer"],
        "intent": final.get("intent"),
        "status": status,
        "escalated": conv.status == "escalated",
        "ticket": (final["memory"] or {}).get("ticket_ref"),
        "citations": final.get("citations") or [],
        "tool_calls": [t.summary() for t in deps.tool_log],
        "trace": final.get("trace") or [],
        "validation": final.get("validation"),
        "metrics": {
            "latency_ms": round(latency_ms, 1), "llm_ms": round(m.llm_ms, 1),
            "retrieval_ms": round(m.retrieval_ms, 1), "tool_ms": round(m.tool_ms, 1),
            "retrieved_chunks": m.retrieved_count, "model": m.model or c.llm.model, "cache_hit": m.cache_hit,
            "prompt_tokens": m.prompt_tokens or None, "completion_tokens": m.completion_tokens or None,
        },
    }
