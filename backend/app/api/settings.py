"""Settings page API: change the language model, embeddings and database without editing files or restarting."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_container, require_admin
from app.rag.embeddings import EmbeddingError
from app.rag.ingestion import reindex_all
from app.services import settings_service as svc
from app.services.container import Container

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_admin)])


def _writable(c: Container) -> None:
    if not c.settings.allow_runtime_config:
        raise HTTPException(403, "Changing settings from the UI is disabled (ALLOW_RUNTIME_CONFIG=false).")


class SettingsUpdate(BaseModel):
    llm_provider: Literal["ollama", "openai_compatible", "extractive"] | None = None
    ollama_base_url: str | None = Field(default=None, max_length=300)
    ollama_model: str | None = Field(default=None, max_length=120)
    ollama_num_ctx: int | None = None
    openai_base_url: str | None = Field(default=None, max_length=300)
    openai_api_key: str | None = Field(default=None, max_length=300)  # None/masked = keep, "" = clear
    openai_model: str | None = Field(default=None, max_length=120)
    embedding_provider: Literal["ollama", "openai_compatible", "hash"] | None = None
    embedding_model: str | None = Field(default=None, max_length=120)
    database_url: str | None = Field(default=None, max_length=500)
    seed_demo_data: bool = False


class DatabaseTest(BaseModel):
    database_url: str = Field(max_length=500)


class ModelTest(SettingsUpdate):
    pass


@router.get("")
def get_settings(c: Container = Depends(get_container)):
    return svc.describe(c)


@router.get("/ollama-models")
def ollama_models(base_url: str):
    try:
        return svc.list_ollama_models(base_url)
    except svc.SettingsError as exc:
        raise HTTPException(422, str(exc)) from exc


def _candidate(c: Container, body: SettingsUpdate):
    try:
        return svc.build_candidate(c.settings, body.model_dump(exclude={"seed_demo_data"}, exclude_none=True))
    except svc.SettingsError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/test/llm")
def test_llm(body: ModelTest, c: Container = Depends(get_container)):
    return svc.test_llm(_candidate(c, body))


@router.post("/test/embedding")
def test_embedding(body: ModelTest, c: Container = Depends(get_container)):
    return svc.test_embedding(_candidate(c, body))


@router.post("/test/database")
def test_database(body: DatabaseTest):
    try:
        return svc.test_database(body.database_url)
    except svc.SettingsError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("")
def update_settings(body: SettingsUpdate, request: Request, c: Container = Depends(get_container)):
    _writable(c)
    try:
        return svc.apply_changes(request.app, body.model_dump(exclude={"seed_demo_data"}), seed_demo_data=body.seed_demo_data)
    except svc.SettingsError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/reindex")
def reindex(c: Container = Depends(get_container)):
    """Rebuild the vector index from the chunk text stored in the database, using the current embedding model."""
    _writable(c)
    with c.session_factory() as session:
        try:
            return reindex_all(session, c.store, c.embedder)
        except EmbeddingError as exc:
            raise HTTPException(503, f"Embedding model unavailable: {exc}") from exc


@router.post("/reset")
def reset(request: Request, c: Container = Depends(get_container)):
    """Drop every UI override and go back to the environment / .env values."""
    _writable(c)
    try:
        return svc.reset_to_environment(request.app)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not start with the environment settings: {str(exc).splitlines()[0][:200]}") from exc
