"""Vector store interface + ChromaDB implementation.

The rest of the app depends only on `VectorStore`. To move to pgvector / Qdrant / Pinecone,
implement the same five methods and register it in `get_vector_store`.
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VectorHit:
    id: str
    text: str
    metadata: dict
    similarity: float  # cosine similarity in [-1, 1]


class VectorStore(ABC):
    @abstractmethod
    def add(self, ids: list[str], embeddings: list[list[float]], texts: list[str], metadatas: list[dict]) -> None: ...

    @abstractmethod
    def query(self, embedding: list[float], top_k: int) -> list[VectorHit]: ...

    @abstractmethod
    def delete_document(self, document_id: str) -> None: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def reset(self) -> None: ...


class ChromaVectorStore(VectorStore):
    def __init__(self, path: str, collection: str):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self._client = chromadb.PersistentClient(
            path=path, settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._name = collection
        self._col = self._open()

    def _open(self):
        # HNSW is approximate. A high ef_search makes recall ~exact for knowledge bases of this size
        # (thousands of chunks); with Chroma's default (10) a nearest chunk can be missed outright.
        return self._client.get_or_create_collection(
            self._name,
            configuration={"hnsw": {"space": "cosine", "ef_search": 200, "ef_construction": 200, "max_neighbors": 32}},
        )

    def add(self, ids, embeddings, texts, metadatas):
        clean = [{k: v for k, v in m.items() if v is not None} for m in metadatas]
        self._col.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=clean)

    def query(self, embedding, top_k):
        n = self._col.count()
        if n == 0:
            return []
        res = self._col.query(query_embeddings=[embedding], n_results=min(top_k, n))
        return [
            VectorHit(i, d, m or {}, 1.0 - dist)  # chroma cosine distance = 1 - similarity
            for i, d, m, dist in zip(
                res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True
            )
        ]

    def delete_document(self, document_id):
        self._col.delete(where={"document_id": document_id})

    def count(self):
        return self._col.count()

    def reset(self):
        self._client.delete_collection(self._name)
        self._col = self._open()


def get_vector_store(settings, embedding_model: str) -> VectorStore:
    if settings.vector_store == "chroma":
        # One collection per (embedding model, database): vectors from different models/dims never mix, and each
        # database keeps its own knowledge base, so switching databases never serves another one's documents.
        from app.database.session import db_fingerprint

        suffix = re.sub(r"[^a-zA-Z0-9_-]", "_", embedding_model)[:40]
        return ChromaVectorStore(settings.chroma_path, f"kb_{suffix}_{db_fingerprint(settings.database_url)}")
    raise ValueError(f"Unknown VECTOR_STORE: {settings.vector_store}")
