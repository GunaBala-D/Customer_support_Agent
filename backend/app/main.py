import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, chat, documents, inbox, orders
from app.api import settings as settings_api
from app.config.settings import Settings, get_settings
from app.middleware.request_context import RateLimitMiddleware, RequestContextMiddleware
from app.rag.embeddings import EmbeddingError
from app.services.bootstrap import bootstrap, ensure_users
from app.services.container import Container, build_container
from app.services.runtime_config import ensure_jwt_secret


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = ensure_jwt_secret(settings or get_settings())
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or build_container(settings)
        log = logging.getLogger(__name__)
        if settings.auto_seed:
            try:
                bootstrap(app.state.container)
            except EmbeddingError as exc:  # e.g. first start before Ollama is running or the embedding model is pulled
                log.warning("Sample data was loaded but the documents could not be indexed (%s). Start Ollama, run `ollama pull %s`, "
                            "then restart or use Settings > Rebuild index. The app is running without a knowledge base meanwhile.",
                            exc, settings.embedding_model)
                with app.state.container.session_factory() as s:
                    ensure_users(s, app.state.container.settings)
        else:  # after a migration the users table may be new: make sure someone can log in
            with app.state.container.session_factory() as s:
                ensure_users(s, app.state.container.settings)
        from app.rag.ingestion import index_is_stale, reindex_all

        cont = app.state.container
        with cont.session_factory() as s:  # e.g. first start after switching databases: rebuild the index from stored chunks
            try:
                if index_is_stale(s, cont.store):
                    reindex_all(s, cont.store, cont.embedder)
            except EmbeddingError as exc:
                log.warning("vector index is out of date and could not be rebuilt: %s", exc)
        yield

    app = FastAPI(
        title="Local AI Customer Support & Operations Agent",
        version="1.0.0",
        description="RAG + tool-calling support agent running on a local Ollama model.",
        lifespan=lifespan,
    )
    # Starlette runs the last-added middleware first: request-id wraps everything.
    app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Customer-Id", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    for r in (auth.router, chat.router, documents.router, orders.router, inbox.router, settings_api.router, admin.router):
        app.include_router(r)
    return app


app = create_app()
