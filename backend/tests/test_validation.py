from app.agents.deps import AgentDeps, new_memory
from app.agents.graph import SupportGraph
from app.agents.validator import grounding_ratio, validate_knowledge, validate_tool
from app.rag.pipeline import RAGPipeline
from app.rag.retriever import RetrievedChunk
from app.services.llm.base import LLMProvider, LLMResult


def chunk(text):
    return RetrievedChunk("c", "d", "refund_policy.md", None, "Refund", text, 0.5, 0.5, 0.6)


CTX = [chunk("Products can be returned within 30 days of delivery. Refunds take 5 to 7 business days.")]
CITE = [{"label": "refund_policy.md"}]


def test_grounded_answer_passes():
    v = validate_knowledge("Products can be returned within 30 days of delivery. [1]", CTX, CITE, 0.6, 0.6)
    assert v.passed and v.grounding_ratio >= 0.9 and all(v.checks.values())


def test_wrong_number_is_caught_even_if_words_overlap():
    ratio, bad = grounding_ratio("Products can be returned within 14 days of delivery.", CTX)
    assert ratio > 0.6 and bad == ["14"]
    v = validate_knowledge("Products can be returned within 14 days of delivery. [1]", CTX, CITE, 0.6, 0.6)
    assert not v.passed and v.checks["numbers_supported"] is False


def test_invented_content_fails_grounding():
    v = validate_knowledge("We offer lifetime free replacement on every purchase. [1]", CTX, CITE, 0.6, 0.6)
    assert not v.passed and v.checks["grounded"] is False


def test_missing_citation_or_evidence_fails():
    assert not validate_knowledge("Products can be returned within 30 days.", CTX, [], 0.6, 0.6).checks["citations_present"]
    v = validate_knowledge("", [], [], 0.1, 0.6)
    assert not v.passed and not v.checks["evidence_retrieved"] and v.reasons


def test_tool_validation_distinguishes_business_errors_from_failures():
    ok = [{"success": True}]
    not_found = [{"success": False, "error_code": "not_found"}]
    broken = [{"success": False, "error_code": "internal_error"}]
    assert validate_tool(ok, "a").passed and validate_tool(not_found, "a").passed
    assert not validate_tool(broken, "a").passed and not validate_tool(ok, "").passed
    assert validate_tool([], "Which order?").passed  # no tool needed


class StubLLM(LLMProvider):
    name, model = "stub", "stub-1"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kw):
        return LLMResult(self.reply, self.model, 1.0, 10, 5)

    def health(self):
        return {"ok": True}


def run_graph(container, session, reply, message="How long do I have to return a product?", memory=None):
    llm = StubLLM(reply)
    rag = RAGPipeline(container.rag.retriever, llm, container.settings.rag_max_context_chars)
    deps = AgentDeps(session, container.settings, llm, rag, container.cache, container.events, "conv-v", "CUST-1001")
    container.cache.delete("kb:version")
    return SupportGraph(deps).build().invoke(
        {"message": message, "history": [], "memory": memory or new_memory(), "trace": [], "citations": []})


def test_hallucinated_policy_is_replaced_by_fallback(container, session):
    out = run_graph(container, session, "You can return products within 90 days, no questions asked. [1]")
    assert out["status"] == "fallback" and out["citations"] == []
    assert "couldn't find reliable information" in out["answer"] and "90" not in out["answer"]
    assert out["validation"]["passed"] is False
    assert out["memory"]["pending_action"]["type"] == "escalate_offer"


def test_model_saying_insufficient_context_triggers_fallback(container, session):
    out = run_graph(container, session, "INSUFFICIENT_CONTEXT")
    assert out["status"] == "fallback"


def test_second_consecutive_failure_escalates_automatically(container, session):
    mem = new_memory()
    first = run_graph(container, session, "INSUFFICIENT_CONTEXT", memory=mem)
    assert not first.get("escalated")
    second = run_graph(container, session, "INSUFFICIENT_CONTEXT", memory=first["memory"])
    assert second["escalated"] and second["status"] == "escalated" and second["memory"]["ticket_ref"]


def test_good_answer_resets_failure_counter(container, session):
    mem = new_memory()
    mem["fallback_count"] = 1
    out = run_graph(container, session, "Products can be returned within 30 days of delivery. [1]", memory=mem)
    assert out["status"] == "answered" and out["memory"]["fallback_count"] == 0
    assert out["citations"][0]["filename"] == "refund_policy.md"


def test_llm_outage_degrades_gracefully(container, session):
    from app.services.llm.base import LLMError

    class Down(StubLLM):
        def chat(self, messages, **kw):
            raise LLMError("connection refused")

    llm = Down("")
    rag = RAGPipeline(container.rag.retriever, llm, 3000)
    deps = AgentDeps(session, container.settings, llm, rag, container.cache, container.events, "conv-d", None)
    container.cache.delete("kb:version")
    out = SupportGraph(deps).build().invoke(
        {"message": "How long do I have to return a product?", "history": [], "memory": new_memory(), "trace": [], "citations": []})
    assert out["status"] == "error" and "trouble reaching the language model" in out["answer"]


def test_numbered_list_formatting_is_not_treated_as_unsupported_numbers():
    answer = '1. Use the "Forgot password" link.\n2. The reset link is valid for 30 days.\n3. Agents never ask for it.'
    ratio, bad = grounding_ratio(answer, CTX)
    assert bad == []  # only the real claim "30" is checked, and it is in the context
    ratio2, bad2 = grounding_ratio("1. Returns take 14 days.", CTX)
    assert bad2 == ["14"]


def test_embedding_outage_degrades_gracefully(container, session):
    from app.rag.embeddings import EmbeddingError

    class DownRetriever:
        def retrieve(self, q):
            raise EmbeddingError("connection refused")

    rag = RAGPipeline(container.rag.retriever, StubLLM("x"), 3000)
    rag.retrieve = DownRetriever().retrieve
    deps = AgentDeps(session, container.settings, StubLLM("x"), rag, container.cache, container.events, "conv-e", None)
    out = SupportGraph(deps).build().invoke(
        {"message": "Which payment methods do you accept?", "history": [], "memory": new_memory(), "trace": [], "citations": []})
    assert out["status"] == "error" and "trouble reaching the language model" in out["answer"]
    assert out["memory"]["pending_action"]["type"] == "escalate_offer"  # offers a human instead of a stack trace
