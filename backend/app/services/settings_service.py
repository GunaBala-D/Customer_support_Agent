"""Validation, connection tests and the live hot-swap behind the Settings page.

Every change is tested *before* it is applied, built into a brand-new container, and only then swapped in, so a wrong
URL or an unreachable model server can never leave the running app half-configured.
"""
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.config.settings import REPO_ROOT, Settings
from app.database.migrate import database_state
from app.rag.embeddings import EmbeddingError, get_embedder
from app.rag.ingestion import index_is_stale, reindex_all
from app.services import runtime_config
from app.services.bootstrap import bootstrap, ensure_users
from app.services.container import Container, build_container
from app.services.llm import get_llm
from app.services.llm.base import LLMError

SWAP_LOCK = threading.Lock()
LLM_PROVIDERS = ("ollama", "openai_compatible", "extractive")
EMBEDDING_PROVIDERS = ("ollama", "openai_compatible", "hash")


class SettingsError(ValueError):
    """A problem with what the admin entered; the message is safe to show in the UI."""


def normalize_database_url(raw: str) -> str:
    raw = (raw or "").strip()
    for prefix in ("postgresql://", "postgres://"):
        if raw.startswith(prefix):
            raw = "postgresql+psycopg://" + raw[len(prefix):]
    try:
        url = make_url(raw)
    except ArgumentError as exc:
        raise SettingsError("That doesn't look like a database URL.") from exc
    if url.drivername not in ("sqlite", "postgresql+psycopg"):
        raise SettingsError("Supported databases: SQLite (sqlite:///path/to/file.db) and PostgreSQL (postgresql+psycopg://user:password@host:5432/dbname).")
    if url.drivername == "sqlite":
        if not url.database or url.database == ":memory:":
            raise SettingsError("Use a SQLite file path; in-memory databases lose everything on restart.")
        path = Path(url.database)
        if not path.is_absolute():  # relative paths live under data/
            path = REPO_ROOT / "data" / path
        return f"sqlite:///{path.as_posix()}"
    if not url.host or not url.database or not url.username:
        raise SettingsError("PostgreSQL needs a host, database name and user.")
    return raw


def _check_http_url(value: str, label: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SettingsError(f"{label} must be an http(s) URL, e.g. http://127.0.0.1:11434")
    return value


def test_database(url: str) -> dict:
    url = normalize_database_url(url)
    started = time.perf_counter()
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"connect_args": {"connect_timeout": 5}}
    engine = create_engine(url, **kwargs)
    try:
        if url.startswith("sqlite"):
            Path(make_url(url).database).parent.mkdir(parents=True, exist_ok=True)
        with engine.connect() as c:
            c.execute(text("select 1"))
            version = c.execute(text("select version()")).scalar() if not url.startswith("sqlite") else "SQLite " + c.execute(text("select sqlite_version()")).scalar()
        state = database_state(engine)
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin as text
        first_line = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:240]
        return {"ok": False, "error": first_line, "url": runtime_config.mask_database_url(url)}
    finally:
        engine.dispose()
    friendly = {
        "empty": "Empty database: the tables will be created on apply.",
        "legacy": "Existing application tables found: they will be adopted and migrated, data kept.",
        "behind": "Application database from an older version: it will be migrated.",
        "current": "Application database, up to date.",
        "conflict": "Contains only some of the application's tables, so the app will refuse to use it.",
        "unknown_revision": "Created by a newer version of the app; refusing to use it.",
    }[state["state"]]
    return {"ok": state["state"] not in ("conflict", "unknown_revision"), "url": runtime_config.mask_database_url(url), "server": version,
            "state": state["state"], "message": friendly, "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": None if state["state"] not in ("conflict", "unknown_revision") else friendly}


def list_ollama_models(base_url: str) -> dict:
    base_url = _check_http_url(base_url, "Ollama URL")
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = sorted(m["name"] for m in resp.json().get("models", []))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"ok": False, "models": [], "error": f"Could not reach Ollama at {base_url}: {exc}"}
    return {"ok": True, "models": models, "error": None}


def test_llm(settings: Settings) -> dict:
    if settings.llm_provider == "extractive":
        return {"ok": True, "message": "Offline test double: no model server involved.", "latency_ms": 0}
    try:
        provider = get_llm(settings)
        health = provider.health()
        if not health["ok"]:
            return {"ok": False, "error": health["error"] or "model server not ready"}
        res = provider.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=8, temperature=0)
    except (LLMError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": True, "model": res.model, "sample": res.text[:60], "latency_ms": round(res.latency_ms)}


def test_embedding(settings: Settings) -> dict:
    try:
        started = time.perf_counter()
        vec = get_embedder(settings).embed_query("hello")
    except (EmbeddingError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": True, "dimension": len(vec), "model": settings.embedding_model, "latency_ms": round((time.perf_counter() - started) * 1000)}


def describe(c: Container) -> dict:
    s = c.settings
    with c.session_factory() as session:
        from sqlalchemy import func, select

        from app.models.db import Document, DocumentChunk

        docs = session.scalar(select(func.count()).select_from(Document)) or 0
        chunks = session.scalar(select(func.count()).select_from(DocumentChunk)) or 0
        stale = index_is_stale(session, c.store)
    file_overrides = runtime_config.load(s.runtime_config_path)
    return {
        "editable": s.allow_runtime_config,
        "llm": {"provider": s.llm_provider, "ollama_base_url": s.ollama_base_url, "ollama_model": s.ollama_model, "ollama_num_ctx": s.ollama_num_ctx,
                "openai_base_url": s.openai_base_url, "openai_api_key": runtime_config.mask("openai_api_key", s.openai_api_key),
                "openai_model": s.openai_model, "health": c.llm.health()},
        "embedding": {"provider": s.embedding_provider, "model": s.embedding_model},
        "database": {"url": runtime_config.mask_database_url(s.database_url), "dialect": make_url(s.database_url).get_backend_name()},
        "knowledge_base": {"documents": docs, "chunks": chunks, "indexed": c.store.count(), "needs_reindex": stale},
        "overridden_keys": sorted(k for k in file_overrides if k != "jwt_secret"),
        "providers": {"llm": list(LLM_PROVIDERS), "embedding": list(EMBEDDING_PROVIDERS)},
    }


def build_candidate(current: Settings, changes: dict) -> Settings:
    """Validate the requested changes and return the settings they would produce. Raises SettingsError."""
    clean: dict = {}
    for key, value in changes.items():
        if value is None:
            continue
        if key == "openai_api_key" and (value == "" or str(value).startswith("••••")):
            if value == "":
                clean[key] = ""  # explicit clear
            continue  # masked placeholder echoed back by the UI: keep the stored secret
        clean[key] = value
    if "llm_provider" in clean and clean["llm_provider"] not in LLM_PROVIDERS:
        raise SettingsError(f"Unknown LLM provider. Choose one of: {', '.join(LLM_PROVIDERS)}")
    if "embedding_provider" in clean and clean["embedding_provider"] not in EMBEDDING_PROVIDERS:
        raise SettingsError(f"Unknown embedding provider. Choose one of: {', '.join(EMBEDDING_PROVIDERS)}")
    for key, label in (("ollama_base_url", "Ollama URL"), ("openai_base_url", "OpenAI-compatible base URL")):
        if clean.get(key):
            clean[key] = _check_http_url(clean[key], label)
    if "database_url" in clean:
        clean["database_url"] = normalize_database_url(clean["database_url"])
    if "ollama_num_ctx" in clean and not 512 <= int(clean["ollama_num_ctx"]) <= 131072:
        raise SettingsError("Context window must be between 512 and 131072 tokens.")
    for key in ("ollama_model", "openai_model", "embedding_model"):
        if key in clean:
            clean[key] = str(clean[key]).strip()
    try:
        candidate = Settings(**{**current.model_dump(), **clean})
    except ValueError as exc:
        raise SettingsError(str(exc).splitlines()[-1][:200]) from exc
    _require_fields_of_selected_providers(candidate)
    return candidate


def _require_fields_of_selected_providers(s: Settings) -> None:
    """Blank fields are fine for providers that are not selected; the selected ones need their settings."""
    needs: list[tuple[str, str]] = []
    if s.llm_provider == "ollama":
        needs += [(s.ollama_base_url, "Ollama URL"), (s.ollama_model, "Ollama model")]
    elif s.llm_provider == "openai_compatible":
        needs += [(s.openai_base_url, "OpenAI-compatible base URL"), (s.openai_model, "OpenAI-compatible model")]
    if s.embedding_provider == "ollama":
        needs += [(s.ollama_base_url, "Ollama URL"), (s.embedding_model, "Embedding model")]
    elif s.embedding_provider == "openai_compatible":
        needs += [(s.openai_base_url, "OpenAI-compatible base URL"), (s.embedding_model, "Embedding model")]
    for value, label in needs:
        if not str(value).strip():
            raise SettingsError(f"{label} is required for the selected provider.")


def apply_changes(app, changes: dict, *, seed_demo_data: bool = False) -> dict:
    """Test -> build new container (runs migrations) -> persist -> swap. Nothing changes if any step fails."""
    with SWAP_LOCK:
        old: Container = app.state.container
        candidate = build_candidate(old.settings, changes)
        db_changed = candidate.database_url != old.settings.database_url
        report: dict = {"tests": {}}

        if (candidate.llm_provider, candidate.ollama_base_url, candidate.ollama_model, candidate.openai_base_url,
                candidate.openai_model, candidate.openai_api_key, candidate.ollama_num_ctx) != (
                old.settings.llm_provider, old.settings.ollama_base_url, old.settings.ollama_model, old.settings.openai_base_url,
                old.settings.openai_model, old.settings.openai_api_key, old.settings.ollama_num_ctx):
            report["tests"]["llm"] = test_llm(candidate)
            if not report["tests"]["llm"]["ok"]:
                raise SettingsError("Language model test failed: " + report["tests"]["llm"]["error"])
        if (candidate.embedding_provider, candidate.embedding_model, candidate.ollama_base_url, candidate.openai_base_url) != (
                old.settings.embedding_provider, old.settings.embedding_model, old.settings.ollama_base_url, old.settings.openai_base_url):
            report["tests"]["embedding"] = test_embedding(candidate)
            if not report["tests"]["embedding"]["ok"]:
                raise SettingsError("Embedding model test failed: " + report["tests"]["embedding"]["error"])
        if db_changed:
            report["tests"]["database"] = test_database(candidate.database_url)
            if not report["tests"]["database"]["ok"]:
                raise SettingsError("Database test failed: " + report["tests"]["database"]["error"])

        try:
            new = build_container(candidate)  # connects, migrates, builds providers
        except Exception as exc:  # noqa: BLE001
            raise SettingsError(f"Could not start with these settings: {str(exc).splitlines()[0][:240]}") from exc

        with new.session_factory() as session:
            ensure_users(session, candidate)  # a new database needs someone who can sign in
        if db_changed and seed_demo_data:
            bootstrap(new)
        with new.session_factory() as session:
            if index_is_stale(session, new.store):
                try:
                    report["reindexed"] = reindex_all(session, new.store, new.embedder)
                except EmbeddingError as exc:
                    report["reindex_error"] = str(exc)[:200]

        stored = runtime_config.load(old.settings.runtime_config_path)
        for key in runtime_config.EDITABLE_KEYS:
            if key in changes and changes[key] is not None and getattr(candidate, key) != getattr(old.settings, key):
                stored[key] = getattr(candidate, key)
        runtime_config.save(old.settings.runtime_config_path, stored)

        app.state.container = new
        old.engine.dispose()
        report.update(applied=True, relogin_required=db_changed, database_changed=db_changed)
        return report


def reset_to_environment(app) -> dict:
    with SWAP_LOCK:
        old: Container = app.state.container
        stored = runtime_config.load(old.settings.runtime_config_path)
        kept = {k: v for k, v in stored.items() if k not in runtime_config.EDITABLE_KEYS}
        runtime_config.save(old.settings.runtime_config_path, kept)
        base = Settings(**{**Settings().model_dump(), "jwt_secret": old.settings.jwt_secret, "runtime_config_path": old.settings.runtime_config_path})
        new = build_container(base)
        with new.session_factory() as session:
            ensure_users(session, base)
        db_changed = base.database_url != old.settings.database_url
        app.state.container = new
        old.engine.dispose()
        return {"applied": True, "relogin_required": db_changed}

