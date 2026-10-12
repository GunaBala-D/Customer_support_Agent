"""Settings that an admin can change from the UI, persisted in a small JSON file.

Precedence (lowest -> highest):  defaults  <  environment / .env  <  runtime file (Settings page)
The file lives outside the database because the database URL is itself one of the settings.
It holds secrets (database password, API keys), so it is gitignored and written with owner-only permissions.
"""
import json
import logging
import os
import tempfile
from pathlib import Path

from app.config.settings import Settings
from app.services.security import MIN_SECRET_LENGTH, generate_secret

log = logging.getLogger(__name__)

EDITABLE_KEYS = (
    "llm_provider", "ollama_base_url", "ollama_model", "ollama_num_ctx",
    "openai_base_url", "openai_api_key", "openai_model",
    "embedding_provider", "embedding_model", "database_url",
)
SECRET_KEYS = frozenset({"openai_api_key", "database_url", "jwt_secret"})
_ALLOWED_IN_FILE = set(EDITABLE_KEYS) | {"jwt_secret"}


def load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if k in _ALLOWED_IN_FILE} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".runtime_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in data.items() if k in _ALLOWED_IN_FILE}, fh, indent=2)
        try:
            os.chmod(tmp, 0o600)  # owner-only where the OS supports it
        except OSError:
            pass
        os.replace(tmp, target)  # atomic: a crash never leaves a half-written file
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def merge(settings: Settings, overrides: dict) -> Settings:
    clean = {k: v for k, v in overrides.items() if k in _ALLOWED_IN_FILE and v is not None}
    return Settings(**{**settings.model_dump(), **clean}) if clean else settings


def load_effective_settings() -> Settings:
    """What the process runs with: env/.env, then the runtime file on top."""
    base = Settings()
    return merge(base, load(base.runtime_config_path))


def ensure_jwt_secret(settings: Settings) -> Settings:
    if len(settings.jwt_secret) >= MIN_SECRET_LENGTH:
        return settings
    if settings.is_production:
        raise RuntimeError(f"JWT_SECRET must be set to a random value of at least {MIN_SECRET_LENGTH} characters in production")
    stored = load(settings.runtime_config_path)
    secret = stored.get("jwt_secret", "")
    if len(secret) < MIN_SECRET_LENGTH:
        secret = generate_secret()
        try:
            save(settings.runtime_config_path, {**stored, "jwt_secret": secret})
        except OSError as exc:  # read-only filesystem: tokens simply won't survive a restart
            log.warning("could not persist the generated JWT secret: %s", exc)
    return settings.model_copy(update={"jwt_secret": secret})


def mask(key: str, value) -> str | int | None:
    """Never send secrets back to the browser."""
    if value in (None, ""):
        return value
    text = str(value)
    if key == "database_url":
        return mask_database_url(text)
    if key in SECRET_KEYS:
        return "••••" + text[-4:] if len(text) > 8 else "••••"
    return value


def mask_database_url(url: str) -> str:
    """postgresql+psycopg://user:secret@host/db -> postgresql+psycopg://user:••••@host/db"""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:••••@{host}" if ":" in creds else url
