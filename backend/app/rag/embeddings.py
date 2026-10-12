"""Embedding providers behind one interface.

Default: `nomic-embed-text` served by Ollama. Chosen because it is small (~270 MB, 768 dims),
fast on CPU / Apple Silicon, has an 8k-token window and strong retrieval scores for its size.
It expects task prefixes ("search_document: " / "search_query: "), which are added here.

`HashEmbeddings` is a deterministic, dependency-free *lexical* embedding (feature hashing of
unigrams + bigrams). It is NOT semantic; it exists so tests, CI and offline demos run without
a model server, and is labelled as such in evaluation reports.
"""
import hashlib
import math
from abc import ABC, abstractmethod

import httpx

from app.rag.text import terms


class EmbeddingError(Exception):
    pass


class EmbeddingProvider(ABC):
    model: str

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbeddings(EmbeddingProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._prefixed = "nomic" in model

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        try:
            resp = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": inputs},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"Ollama embedding request failed: {exc}") from exc

    def embed_documents(self, texts):
        prefix = "search_document: " if self._prefixed else ""
        out: list[list[float]] = []
        for i in range(0, len(texts), 16):
            out.extend(self._embed([prefix + t for t in texts[i : i + 16]]))
        return out

    def embed_query(self, text):
        prefix = "search_query: " if self._prefixed else ""
        return self._embed([prefix + text])[0]


class OpenAICompatEmbeddings(EmbeddingProvider):
    """POST {base}/embeddings (OpenAI protocol). No task prefixes: use a model that does not need them."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 120.0):
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = httpx.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": inputs}, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"OpenAI-compatible embedding request failed: {exc}") from exc

    def embed_documents(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            out.extend(self._embed(texts[i : i + 64]))
        return out

    def embed_query(self, text):
        return self._embed([text])[0]


class HashEmbeddings(EmbeddingProvider):
    def __init__(self, dim: int = 768):
        self.dim = dim
        self.model = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        toks = terms(text)
        feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for f in feats:
            counts[f] = counts.get(f, 0) + 1
        for f, c in counts.items():
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += (1 if (h >> 63) & 1 else -1) * (1 + math.log(c))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def get_embedder(settings) -> EmbeddingProvider:
    if settings.embedding_provider == "ollama":
        return OllamaEmbeddings(settings.ollama_base_url, settings.embedding_model)
    if settings.embedding_provider == "openai_compatible":
        if not settings.openai_base_url:
            raise ValueError("openai_compatible embeddings need a base URL")
        return OpenAICompatEmbeddings(settings.openai_base_url, settings.embedding_model, settings.openai_api_key)
    if settings.embedding_provider == "hash":
        return HashEmbeddings()
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider}")
