"""Response validation. Pure functions so every check is unit-testable.

Metrics exposed to users and the dashboard, with their definitions:
  relevance_score  = top blended retrieval score (0.5*cosine + 0.5*query-term coverage), 0..1
  grounding_ratio  = share of the answer's content terms that appear in the cited context, 0..1
No "AI confidence" number is produced: neither value is a probability, and the docs say so.
"""
import re
from dataclasses import dataclass, field

from app.rag.retriever import RetrievedChunk
from app.rag.text import terms

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_MARKER_RE = re.compile(r"\[\d+\]")
_LIST_NUMBER_RE = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")


@dataclass
class ValidationResult:
    passed: bool
    checks: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    grounding_ratio: float | None = None
    relevance_score: float | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "reasons": self.reasons,
            "grounding_ratio": self.grounding_ratio,
            "relevance_score": self.relevance_score,
        }


def grounding_ratio(answer: str, chunks: list[RetrievedChunk]) -> tuple[float, list[str]]:
    """Return (ratio, unsupported_numbers). Numbers are checked separately and strictly:
    a wrong '14 days' vs '30 days' is the classic policy hallucination."""
    body = _MARKER_RE.sub(" ", answer)
    body = _LIST_NUMBER_RE.sub(" ", body)  # "1. Do this" numbering is formatting, not a claimed fact
    answer_terms = set(terms(body))
    context = " ".join(c.text for c in chunks)
    context_terms = set(terms(context))
    ratio = len(answer_terms & context_terms) / len(answer_terms) if answer_terms else 0.0
    context_numbers = set(_NUM_RE.findall(context))
    bad_numbers = [n for n in _NUM_RE.findall(body) if n not in context_numbers]
    return ratio, bad_numbers


def validate_knowledge(
    answer: str,
    chunks: list[RetrievedChunk],
    citations: list[dict],
    top_score: float | None,
    min_grounding: float,
) -> ValidationResult:
    res = ValidationResult(True, relevance_score=top_score)
    res.checks["evidence_retrieved"] = bool(chunks)
    if not chunks:
        res.reasons.append("no chunk passed the relevance threshold")
    res.checks["answer_present"] = bool(answer.strip())
    if not answer.strip():
        res.reasons.append("model produced no answer")
    res.checks["citations_present"] = bool(citations)
    if not citations:
        res.reasons.append("answer has no citation")
    if chunks and answer.strip():
        ratio, bad_numbers = grounding_ratio(answer, chunks)
        res.grounding_ratio = round(ratio, 3)
        res.checks["grounded"] = ratio >= min_grounding
        res.checks["numbers_supported"] = not bad_numbers
        if ratio < min_grounding:
            res.reasons.append(f"grounding ratio {ratio:.2f} below {min_grounding:.2f}")
        if bad_numbers:
            res.reasons.append(f"numbers not found in context: {bad_numbers}")
    res.passed = all(v for v in res.checks.values())
    return res


def validate_tool(tool_results: list[dict], answer: str) -> ValidationResult:
    res = ValidationResult(True)
    res.checks["tool_returned_valid_data"] = all(
        r["success"] or r.get("error_code") in {"not_found", "not_cancellable", "already_cancelled", "auth_required", "forbidden", "invalid_arguments"}
        for r in tool_results
    )
    # Expected business outcomes (order not found, not cancellable) are valid answers.
    # Infrastructure failures (internal_error, unknown_tool) are not.
    if not res.checks["tool_returned_valid_data"]:
        res.reasons.append("tool failed unexpectedly")
    res.checks["answer_present"] = bool(answer.strip())
    res.passed = all(res.checks.values())
    return res
