"""Retriever with a lightweight reranker.

score = 0.5 * vector_similarity + 0.5 * query_term_coverage

- vector_similarity: cosine similarity from the vector store (clamped to >= 0)
- query_term_coverage: share of the query's content terms that occur in the chunk

Candidates (`rag_candidates`) are fetched from the vector store, re-scored with the blend, and
only chunks with score >= `rag_min_score` count as evidence. The lexical term guards against
the classic failure of dense retrieval, where an off-topic question still returns the "nearest"
chunk. The threshold is a setting because it depends on the embedding model.
"""
import time
from dataclasses import asdict, dataclass, field

from app.rag.embeddings import EmbeddingProvider
from app.rag.text import terms
from app.rag.vectorstore import VectorStore


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    page: int | None
    section: str | None
    text: str
    vector_score: float
    lexical_score: float
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RetrievalResult:
    query: str
    ranked: list[RetrievedChunk] = field(default_factory=list)  # all candidates, best first
    relevant: list[RetrievedChunk] = field(default_factory=list)  # above threshold, top_k
    latency_ms: float = 0.0

    @property
    def top_score(self) -> float | None:
        return self.ranked[0].score if self.ranked else None


class Retriever:
    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider,
        *,
        top_k: int = 4,
        candidates: int = 12,
        min_score: float = 0.30,
    ):
        self.store, self.embedder = store, embedder
        self.top_k, self.candidates, self.min_score = top_k, candidates, min_score

    def retrieve(self, query: str) -> RetrievalResult:
        start = time.perf_counter()
        q_terms = set(terms(query))
        hits = self.store.query(self.embedder.embed_query(query), self.candidates)
        ranked = []
        for h in hits:
            chunk_terms = set(terms(h.text))
            lexical = len(q_terms & chunk_terms) / len(q_terms) if q_terms else 0.0
            vector = max(h.similarity, 0.0)
            ranked.append(
                RetrievedChunk(
                    chunk_id=h.id,
                    document_id=h.metadata.get("document_id", ""),
                    filename=h.metadata.get("filename", "unknown"),
                    page=h.metadata.get("page"),
                    section=h.metadata.get("section"),
                    text=h.text,
                    vector_score=round(vector, 4),
                    lexical_score=round(lexical, 4),
                    score=round(0.5 * vector + 0.5 * lexical, 4),
                )
            )
        ranked.sort(key=lambda c: -c.score)
        relevant = [c for c in ranked if c.score >= self.min_score][: self.top_k]
        return RetrievalResult(query, ranked, relevant, (time.perf_counter() - start) * 1000)
