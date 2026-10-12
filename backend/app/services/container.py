"""Wires settings -> engine, LLM, embeddings, vector store, cache, RAG into one object."""
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.database.migrate import upgrade_to_head
from app.database.session import db_fingerprint, make_engine, make_session_factory
from app.rag.embeddings import EmbeddingProvider, get_embedder
from app.rag.pipeline import RAGPipeline
from app.rag.retriever import Retriever
from app.rag.vectorstore import VectorStore, get_vector_store
from app.services.cache import Cache, get_cache
from app.services.llm import LLMProvider, get_llm
from app.services.observability import EventLogger


@dataclass
class Container:
    settings: Settings
    session_factory: sessionmaker[Session]
    llm: LLMProvider
    embedder: EmbeddingProvider
    store: VectorStore
    cache: Cache
    rag: RAGPipeline
    events: EventLogger
    engine: Engine
    db_fingerprint: str


def build_container(settings: Settings, *, llm: LLMProvider | None = None, embedder: EmbeddingProvider | None = None) -> Container:
    engine = make_engine(settings.database_url)
    if settings.run_migrations:
        upgrade_to_head(engine)
    sf = make_session_factory(engine)
    llm = llm or get_llm(settings)
    embedder = embedder or get_embedder(settings)
    store = get_vector_store(settings, embedder.model)
    retriever = Retriever(
        store, embedder, top_k=settings.rag_top_k, candidates=settings.rag_candidates, min_score=settings.rag_min_score
    )
    rag = RAGPipeline(retriever, llm, settings.rag_max_context_chars)
    return Container(settings, sf, llm, embedder, store, get_cache(settings), rag, EventLogger(sf), engine, db_fingerprint(settings.database_url))
