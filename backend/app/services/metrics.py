"""Aggregations for the admin dashboard. Computed from persisted rows, nothing is estimated."""
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import Conversation, Message, ToolCall, utcnow
from app.rag.text import terms

KNOWLEDGE_INTENTS = ("knowledge_question", "refund_question")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def compute_metrics(session: Session, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    convs = session.execute(
        select(Conversation.status, func.count()).where(Conversation.created_at >= since).group_by(Conversation.status)
    ).all()
    by_status = {s: n for s, n in convs}
    total = sum(by_status.values())

    msgs = session.scalars(select(Message).where(Message.role == "assistant", Message.created_at >= since)).all()
    latencies = [m.latency_ms for m in msgs if m.latency_ms is not None]

    tool_rows = session.execute(
        select(ToolCall.tool_name, ToolCall.success, func.count(), func.avg(ToolCall.duration_ms))
        .where(ToolCall.created_at >= since).group_by(ToolCall.tool_name, ToolCall.success)
    ).all()
    per_tool: dict[str, dict] = defaultdict(lambda: {"calls": 0, "success": 0, "avg_ms": 0.0})
    for name, ok, n, avg_ms in tool_rows:
        t = per_tool[name]
        t["calls"] += n
        t["success"] += n if ok else 0
        t["avg_ms"] += (avg_ms or 0) * n
    tools = []
    for name, t in sorted(per_tool.items()):
        tools.append({"tool": name, "calls": t["calls"], "success_rate": round(t["success"] / t["calls"], 3),
                      "avg_ms": round(t["avg_ms"] / t["calls"], 1)})
    total_calls = sum(t["calls"] for t in per_tool.values())
    total_ok = sum(t["success"] for t in per_tool.values())

    rag_msgs = [m for m in msgs if m.intent in KNOWLEDGE_INTENTS and m.retrieved_count is not None]
    scores = [m.top_score for m in rag_msgs if m.top_score is not None]
    retrieval_ms = [m.retrieval_ms for m in rag_msgs if m.retrieval_ms]

    failed = []
    for m in sorted((m for m in msgs if m.response_status in {"fallback", "error"}), key=lambda m: -m.id)[:10]:
        q = session.scalars(
            select(Message).where(Message.conversation_id == m.conversation_id, Message.role == "user", Message.id < m.id)
            .order_by(Message.id.desc()).limit(1)
        ).first()
        failed.append({"question": q.content if q else "", "status": m.response_status, "conversation_id": m.conversation_id,
                       "at": m.created_at.isoformat(timespec="seconds")})

    faq_groups: dict[tuple, list[str]] = defaultdict(list)
    for (content,) in session.execute(
        select(Message.content).where(Message.role == "user", Message.intent.in_(KNOWLEDGE_INTENTS), Message.created_at >= since)
    ):
        faq_groups[tuple(sorted(set(terms(content))))].append(content)
    faq = sorted(({"question": v[0], "count": len(v)} for k, v in faq_groups.items() if k), key=lambda x: -x["count"])[:8]

    daily = Counter(c.date().isoformat() for (c,) in session.execute(
        select(Conversation.created_at).where(Conversation.created_at >= utcnow() - timedelta(days=14))))
    today = utcnow().date()
    series = [{"date": (today - timedelta(days=i)).isoformat(), "conversations": daily.get((today - timedelta(days=i)).isoformat(), 0)}
              for i in range(13, -1, -1)]

    return {
        "window_days": days,
        "conversations": {"total": total, "resolved": by_status.get("resolved", 0),
                          "escalated": by_status.get("escalated", 0), "open": by_status.get("open", 0)},
        "latency_ms": {"avg": _avg(latencies), "p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95),
                       "avg_llm": _avg([m.llm_ms for m in msgs if m.llm_ms]),
                       "avg_retrieval": _avg(retrieval_ms), "avg_tool": _avg([m.tool_ms for m in msgs if m.tool_ms])},
        "tools": {"total_calls": total_calls, "success_rate": round(total_ok / total_calls, 3) if total_calls else None, "by_tool": tools},
        "rag": {"queries": len(rag_msgs), "avg_retrieved_chunks": _avg([m.retrieved_count for m in rag_msgs]),
                "avg_top_score": _avg(scores),
                "no_evidence_rate": round(sum(1 for m in rag_msgs if m.retrieved_count == 0) / len(rag_msgs), 3) if rag_msgs else None},
        "intents": dict(Counter(m.intent for m in msgs if m.intent)),
        "response_status": dict(Counter(m.response_status for m in msgs if m.response_status)),
        "failed_queries": failed,
        "frequent_questions": faq,
        "conversations_per_day": series,
        "tokens": {"prompt": sum(m.prompt_tokens or 0 for m in msgs), "completion": sum(m.completion_tokens or 0 for m in msgs)},
    }
