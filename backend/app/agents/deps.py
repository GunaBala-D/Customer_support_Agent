from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.rag.pipeline import RAGPipeline
from app.services.cache import Cache
from app.services.llm.base import LLMProvider, LLMResult
from app.tools import ToolContext, ToolResult, run_tool


def new_memory() -> dict:
    """Short-term, per-conversation memory. Long-term customer data lives in the DB and is
    only reachable through permission-checked tools, never copied into this dict."""
    return {
        "last_order_id": None,       # resolves "it" / "my order"
        "pending_action": None,      # {"type": "cancel_order" | "escalate_offer", ...} awaiting yes/no
        "awaiting_order_for": None,  # intent that asked "which order?"
        "fallback_count": 0,         # consecutive unanswerable knowledge questions
        "unknown_count": 0,
        "ticket_ref": None,
    }


@dataclass
class RequestMetrics:
    llm_ms: float = 0.0
    retrieval_ms: float = 0.0
    tool_ms: float = 0.0
    retrieved_count: int = 0
    top_score: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    cache_hit: bool = False
    model: str | None = None

    def add_llm(self, r: LLMResult | None) -> None:
        if r is None:
            return
        self.llm_calls += 1
        self.llm_ms += r.latency_ms
        self.prompt_tokens += r.prompt_tokens or 0
        self.completion_tokens += r.completion_tokens or 0
        self.model = r.model


@dataclass
class AgentDeps:
    session: Session
    settings: object
    llm: LLMProvider
    rag: RAGPipeline
    cache: Cache
    events: object
    conversation_id: str
    customer_id: str | None
    metrics: RequestMetrics = field(default_factory=RequestMetrics)
    tool_log: list[ToolResult] = field(default_factory=list)

    def call_tool(self, name: str, args: dict, confirmed: bool = False) -> ToolResult:
        ctx = ToolContext(self.session, self.customer_id, self.conversation_id, False, self.events)
        res = run_tool(ctx, name, args, confirmed)
        self.metrics.tool_ms += res.duration_ms
        self.tool_log.append(res)
        return res
