import os

import pytest
from fastapi.testclient import TestClient

from app.config.settings import REPO_ROOT, Settings
from app.main import create_app
from app.services.bootstrap import bootstrap
from app.services.container import build_container

ADMIN_KEY = "test-admin-key"
JWT_SECRET = "test-jwt-secret-test-jwt-secret-0123456789"
DEMO_PASSWORD = "demo-password"


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_developers_database():
    """Tests build every Settings explicitly. If something falls back to Settings() it would read the developer's .env,
    so make that fallback harmless (throwaway SQLite) for the whole test session."""
    import tempfile

    scratch = tempfile.mkdtemp(prefix="support-tests-")
    patch = pytest.MonkeyPatch()
    patch.setenv("DATABASE_URL", f"sqlite:///{scratch.replace(chr(92), '/')}/env_fallback.db")
    patch.setenv("CHROMA_PATH", f"{scratch.replace(chr(92), '/')}/env_chroma")
    patch.setenv("RUNTIME_CONFIG_PATH", f"{scratch.replace(chr(92), '/')}/runtime_settings.json")
    yield
    patch.undo()


@pytest.fixture(scope="session")
def _base(tmp_path_factory):
    """One seeded DB + one ingested vector store for the whole session (read-mostly)."""
    tmp = tmp_path_factory.mktemp("env")
    # Default: throwaway SQLite. Set TEST_DATABASE_URL=postgresql+psycopg://... to run the suite on PostgreSQL
    # (use a dedicated empty database: its tables are dropped and recreated).
    db_url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(tmp / 'test.db').as_posix()}"
    if db_url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        from app.database.migrate import VERSION_TABLE
        from app.models.db import Base

        engine = create_engine(db_url)
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text(f"drop table if exists {VERSION_TABLE}"))  # otherwise Alembic thinks the schema still exists
        engine.dispose()
    settings = Settings(
        database_url=db_url,
        chroma_path=(tmp / "chroma").as_posix(),
        documents_dir=(REPO_ROOT / "data" / "documents").as_posix(),
        llm_provider="extractive", embedding_provider="hash",
        admin_api_key=ADMIN_KEY, rate_limit_per_minute=1000, redis_url="", app_env="test",
        jwt_secret=JWT_SECRET, runtime_config_path=(tmp / "runtime_settings.json").as_posix(),
    )
    container = build_container(settings)
    bootstrap(container)
    return settings, container


@pytest.fixture
def container(_base):
    return _base[1]


@pytest.fixture
def settings(_base):
    return _base[0]


@pytest.fixture
def session(container):
    with container.session_factory() as s:
        yield s


@pytest.fixture
def client(settings, container):
    app = create_app(settings, container)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin():
    return {"X-API-Key": ADMIN_KEY}


@pytest.fixture
def aisha():
    return {"X-Customer-Id": "CUST-1001"}


@pytest.fixture(autouse=True)
def _reset_demo_orders(container):
    """Tests mutate ORD-1001 (cancel). Put the demo order back before every test."""
    from app.models.db import Order

    with container.session_factory() as s:
        o = s.get(Order, "ORD-1001")
        o.status, o.payment_status, o.notes = "processing", "paid", None
        s.commit()
    container.cache.clear()
    yield


def login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin_jwt(client):
    return login(client, "admin@supportpilot.example", "admin-demo-1")


@pytest.fixture
def agent_jwt(client):
    return login(client, "agent@supportpilot.example", DEMO_PASSWORD)


@pytest.fixture
def customer_jwt(client):
    return login(client, "aisha.khan@example.com", DEMO_PASSWORD)  # CUST-1001
