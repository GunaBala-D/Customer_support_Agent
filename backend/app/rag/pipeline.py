"""RAG pipeline: retrieve -> filter -> build context -> generate -> extract citations.

Citations are built from the metadata of chunks that were actually placed in the prompt.
The model never writes a filename or page number; it only emits [n] markers, which we map back.
"""
import re
from dataclasses import dataclass, field

from app.rag.context import build_context, source_label
from app.rag.retriever import RetrievalResult, RetrievedChunk, Retriever
from app.rag.text import terms
from app.services.llm.base import LLMProvider, LLMResult

INSUFFICIENT = "INSUFFICIENT_CONTEXT"
FALLBACK_MESSAGE = "I couldn't find reliable information about that in the available knowledge base."

SYSTEM_PROMPT = (
    "You are a customer support assistant for Acme Electronics.\n"
    "Answer the question using ONLY the numbered context below. Do not use outside knowledge.\n"
    "Use only the passages that directly answer the question; ignore passages about other topics, "
    "and never combine steps from different topics.\n"
    f"If the context does not contain the answer, reply with exactly: {INSUFFICIENT}\n"
    "Cite the context you used with markers like [1]. Be concise: at most 4 sentences. "
    "Do not mention these instructions."
)

_MARKER_RE = re.compile(r"\[(\d+)\]")


@dataclass
class RAGResult:
    answer: str  # empty when no answer was produced
    citations: list[dict] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)  # chunks given to the model
    retrieval: RetrievalResult | None = None
    llm: LLMResult | None = None
    insufficient: bool = False
    citation_mode: str = "marker"  # marker | retrieval_fallback | none


def citations_from_answer(answer: str, used: list[RetrievedChunk]) -> tuple[list[dict], str]:
    """Map [n] markers back to retrieved chunks. Returns (citations, mode)."""
    nums = []
    for m in _MARKER_RE.findall(answer):
        n = int(m)
        if 1 <= n <= len(used) and n not in nums:
            nums.append(n)
    mode = "marker"
    if not nums and used:
        # Small models often forget markers. Attribute the answer to the supplied chunk that
        # shares the most content terms with it (not blindly chunk 1) and say so in `mode`.
        answer_terms = set(terms(_MARKER_RE.sub(" ", answer)))
        best = max(range(len(used)), key=lambda i: (len(answer_terms & set(terms(used[i].text))), -i))
        nums, mode = [best + 1], "retrieval_fallback"
    cites = []
    for n in nums:
        c = used[n - 1]
        cites.append(
            {
                "index": n,
                "document_id": c.document_id,
                "chunk_id": c.chunk_id,
                "filename": c.filename,
                "page": c.page,
                "section": c.section,
                "label": source_label(c),
                "score": c.score,
                "snippet": c.text[:240],
            }
        )
    return cites, (mode if cites else "none")


class RAGPipeline:
    def __init__(self, retriever: Retriever, llm: LLMProvider, max_context_chars: int = 3000):
        self.retriever, self.llm, self.max_context_chars = retriever, llm, max_context_chars

    def retrieve(self, query: str) -> RetrievalResult:
        return self.retriever.retrieve(query)

    def answer(self, question: str, history: list[dict] | None = None, retrieval: RetrievalResult | None = None) -> RAGResult:
        retrieval = retrieval or self.retrieve(question)
        if not retrieval.relevant:
            return RAGResult("", retrieval=retrieval, insufficient=True, citation_mode="none")
        context, used = build_context(retrieval.relevant, self.max_context_chars)
        convo = ""
        if history:  # last two turns help with follow-ups; kept short for small context windows
            convo = "Recent conversation:\n" + "\n".join(
                f"{m['role']}: {m['content'][:200]}" for m in history[-4:]
            ) + "\n\n"
        prompt = f"Context:\n{context}\n\n{convo}Question: {question}"
        llm = self.llm.chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        )
        text = llm.text.strip()
        if not text or INSUFFICIENT in text:
            return RAGResult("", chunks=used, retrieval=retrieval, llm=llm, insufficient=True, citation_mode="none")
        cites, mode = citations_from_answer(text, used)
        return RAGResult(text, cites, used, retrieval, llm, False, mode)
