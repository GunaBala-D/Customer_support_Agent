"""Application settings. Everything is read from environment variables / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config/settings.py -> repo root is three levels above `app`
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- database ---
    database_url: str = Field(default=f"sqlite:///{(REPO_ROOT / 'data' / 'app.db').as_posix()}")
    run_migrations: bool = True  # apply Alembic migrations when the app starts (set false to run `alembic upgrade head` yourself)
    auto_seed: bool = False  # seed + ingest sample docs on startup if DB is empty

    # --- LLM (provider is swappable; Ollama is the primary one) ---
    llm_provider: str = "ollama"  # ollama | openai_compatible | extractive (offline test double)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_num_ctx: int = 4096
    ollama_timeout_seconds: float = 120.0
    llm_max_tokens: int = 350
    llm_temperature: float = 0.1

    # --- OpenAI-compatible server (optional: LM Studio, vLLM, llama.cpp server, or a hosted API) ---
    openai_base_url: str = ""  # e.g. http://127.0.0.1:1234/v1
    openai_api_key: str = ""
    openai_model: str = ""

    # --- embeddings ---
    embedding_provider: str = "ollama"  # ollama | openai_compatible | hash (offline lexical, tests only)
    embedding_model: str = "nomic-embed-text"

    # --- vector store ---
    vector_store: str = "chroma"
    chroma_path: str = Field(default=(REPO_ROOT / "data" / "chroma").as_posix())

    # --- RAG ---
    documents_dir: str = Field(default=(REPO_ROOT / "data" / "documents").as_posix())
    chunk_size: int = 800
    chunk_overlap: int = 120
    rag_top_k: int = 4
    rag_candidates: int = 12
    rag_min_score: float = 0.30  # blended vector+lexical score; recalibrate with `make evaluate`
    rag_max_context_chars: int = 3000
    min_grounding: float = 0.60  # share of answer terms that must appear in retrieved context

    # --- cache ---
    redis_url: str = ""  # empty -> in-process cache (single worker only)
    cache_ttl_seconds: int = 3600

    # --- security ---
    jwt_secret: str = ""  # >= 32 chars. Empty in development -> generated once and kept in the runtime config file
    jwt_expire_minutes: int = 60
    admin_email: str = "admin@supportpilot.example"
    admin_password: str = ""  # production: the admin is only created when this is set (>= 12 chars)
    seed_demo_users: bool = True  # development only: demo customer/agent/admin logins (never in production)
    demo_password: str = "demo-password"
    allow_demo_identity: bool = True  # accept the X-Customer-Id demo header (never honoured in production)
    allow_api_key: bool = True  # accept X-API-Key as an admin service credential
    allow_runtime_config: bool = True  # let admins change LLM/database from the Settings page
    runtime_config_path: str = Field(default=(REPO_ROOT / "data" / "runtime_settings.json").as_posix())
    admin_api_key: str = ""  # service credential for scripts; empty disables it
    rate_limit_per_minute: int = 30
    max_upload_mb: int = 10

    @model_validator(mode="after")
    def _resolve_relative_paths(self):
        # `./data/app.db` in .env must mean the same file no matter which directory the process starts in.
        prefix = "sqlite:///./"
        if self.database_url.startswith(prefix):
            self.database_url = f"sqlite:///{(REPO_ROOT / self.database_url[len(prefix):]).as_posix()}"
        if not Path(self.chroma_path).is_absolute():
            self.chroma_path = (REPO_ROOT / self.chroma_path).as_posix()
        if not Path(self.documents_dir).is_absolute():
            self.documents_dir = (REPO_ROOT / self.documents_dir).as_posix()
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def demo_identity_enabled(self) -> bool:
        return self.allow_demo_identity and not self.is_production

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    # imported lazily: runtime_config imports Settings from this module
    from app.services.runtime_config import load_effective_settings

    return load_effective_settings()
