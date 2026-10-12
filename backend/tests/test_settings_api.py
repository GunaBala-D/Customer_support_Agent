"""Settings page API. Each test gets its own app, SQLite file, vector store and runtime-config file."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config.settings import REPO_ROOT, Settings
from app.main import create_app
from app.rag.embeddings import OpenAICompatEmbeddings
from app.services import runtime_config
from app.services.bootstrap import bootstrap
from app.services.container import build_container
from app.services.llm.openai_compat import OpenAICompatProvider
from tests.conftest import JWT_SECRET, login

SEEN: list[dict] = []


class FakeOpenAI(BaseHTTPRequestHandler):
    """Implements just enough of the OpenAI protocol: /v1/models, /v1/chat/completions, /v1/embeddings."""

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": "local-model"}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
        if self.path == "/v1/chat/completions":
            if body["model"] == "broken":
                return self._send(500, {"error": "boom"})
            text = '{"intent": "knowledge_question"}' if body.get("response_format") else "Standard shipping takes 3 to 5 business days [1]."
            return self._send(200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 77, "completion_tokens": 9}})
        if self.path == "/v1/embeddings":
            inputs = body["input"]
            return self._send(200, {"data": [{"index": i, "embedding": [float(len(t)), 1.0]} for i, t in reversed(list(enumerate(inputs)))]})
        self._send(404, {})


@pytest.fixture(scope="module")
def fake_openai():
    srv = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


@pytest.fixture
def env(tmp_path):
    """An isolated, fully bootstrapped app: returns (client, admin_headers, helpers)."""
    def make_settings(name="main"):
        return Settings(
            database_url=f"sqlite:///{(tmp_path / (name + '.db')).as_posix()}", chroma_path=(tmp_path / "chroma").as_posix(),
            documents_dir=(REPO_ROOT / "data" / "documents").as_posix(), llm_provider="extractive", embedding_provider="hash",
            admin_api_key="k", jwt_secret=JWT_SECRET, app_env="test", rate_limit_per_minute=1000, redis_url="",
            runtime_config_path=(tmp_path / "runtime_settings.json").as_posix(),
        )

    settings = make_settings()
    container = build_container(settings)
    bootstrap(container)
    app = create_app(settings, container)
    with TestClient(app) as client:
        yield client, login(client, "admin@supportpilot.example", "admin-demo-1"), {"tmp": tmp_path, "make": make_settings, "settings": settings}


def sqlite_url(tmp_path, name):
    return f"sqlite:///{(tmp_path / name).as_posix()}"


# ---- access & secrets -------------------------------------------------------------------------------------------

def test_only_admins_can_read_or_change_settings(env):
    client, admin, _ = env
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers=login(client, "agent@supportpilot.example", "demo-password")).status_code == 403
    assert client.put("/api/settings", json={"ollama_model": "x"}, headers=login(client, "aisha.khan@example.com", "demo-password")).status_code == 403
    assert client.get("/api/settings", headers=admin).status_code == 200


def test_get_describes_current_state_without_leaking_secrets(env):
    client, admin, h = env
    pg = "postgresql+psycopg://support:SuperSecret99@127.0.0.1:1/x"
    container = client.app.state.container
    container.settings = container.settings.model_copy(update={"database_url": pg, "openai_api_key": "sk-live-abcdef123456"})
    try:
        raw = client.get("/api/settings", headers=admin).text
    finally:
        container.settings = h["settings"]
    assert "SuperSecret99" not in raw and "abcdef123456" not in raw and "sk-live" not in raw
    body = json.loads(raw)
    assert body["database"]["dialect"] == "postgresql" and "••••" in body["database"]["url"]
    assert body["knowledge_base"]["documents"] == 7 and body["knowledge_base"]["needs_reindex"] is False
    assert set(body["providers"]["llm"]) == {"ollama", "openai_compatible", "extractive"}


def test_runtime_changes_can_be_disabled_by_the_operator(env):
    client, admin, _ = env
    container = client.app.state.container
    container.settings, original = container.settings.model_copy(update={"allow_runtime_config": False}), container.settings
    try:
        assert client.put("/api/settings", json={"ollama_model": "x"}, headers=admin).status_code == 403
        assert client.post("/api/settings/reindex", headers=admin).status_code == 403
        assert client.get("/api/settings", headers=admin).json()["editable"] is False
    finally:
        container.settings = original


# ---- connection tests -------------------------------------------------------------------------------------------

def test_database_connection_test(env):
    client, admin, h = env
    fresh = client.post("/api/settings/test/database", json={"database_url": sqlite_url(h["tmp"], "brand_new.db")}, headers=admin).json()
    assert fresh["ok"] and fresh["state"] == "empty" and "created" in fresh["message"]
    current = client.post("/api/settings/test/database", json={"database_url": h["settings"].database_url}, headers=admin).json()
    assert current["ok"] and current["state"] == "current"
    unreachable = client.post("/api/settings/test/database", json={"database_url": "postgresql+psycopg://u:p@127.0.0.1:1/nope"}, headers=admin).json()
    assert unreachable["ok"] is False and unreachable["error"] and "p@" not in json.dumps(unreachable)
    for bad in ("mysql://u:p@h/db", "not a url", "sqlite://", "postgresql+psycopg://onlyhost"):
        assert client.post("/api/settings/test/database", json={"database_url": bad}, headers=admin).status_code == 422, bad


def test_conflicting_database_is_reported_and_refused(env):
    import sqlite3

    client, admin, h = env
    path = h["tmp"] / "foreign.db"
    con = sqlite3.connect(path)
    con.execute("create table customers (id integer primary key, secret text)")
    con.commit()
    con.close()
    res = client.post("/api/settings/test/database", json={"database_url": sqlite_url(h["tmp"], "foreign.db")}, headers=admin).json()
    assert res["ok"] is False and res["state"] == "conflict"
    put = client.put("/api/settings", json={"database_url": sqlite_url(h["tmp"], "foreign.db")}, headers=admin)
    assert put.status_code == 422 and "Database test failed" in put.json()["detail"]


def test_llm_test_against_an_openai_compatible_server(env, fake_openai):
    client, admin, _ = env
    ok = client.post("/api/settings/test/llm", headers=admin,
                     json={"llm_provider": "openai_compatible", "openai_base_url": fake_openai, "openai_model": "local-model", "openai_api_key": "sk-test"}).json()
    assert ok["ok"] and ok["model"] == "local-model" and ok["latency_ms"] >= 0
    wrong_model = client.post("/api/settings/test/llm", headers=admin,
                              json={"llm_provider": "openai_compatible", "openai_base_url": fake_openai, "openai_model": "missing"}).json()
    assert wrong_model["ok"] is False and "not offered" in wrong_model["error"]
    down = client.post("/api/settings/test/llm", headers=admin,
                       json={"llm_provider": "openai_compatible", "openai_base_url": "http://127.0.0.1:9/v1", "openai_model": "m"}).json()
    assert down["ok"] is False
    assert client.post("/api/settings/test/llm", json={"openai_base_url": "ftp://x"}, headers=admin).status_code == 422


def test_ollama_model_listing_reports_unreachable_servers(env):
    client, admin, _ = env
    r = client.get("/api/settings/ollama-models?base_url=http://127.0.0.1:9", headers=admin).json()
    assert r["ok"] is False and r["models"] == [] and "Could not reach" in r["error"]
    assert client.get("/api/settings/ollama-models?base_url=javascript:alert(1)", headers=admin).status_code == 422


# ---- applying changes -------------------------------------------------------------------------------------------

def test_switching_the_llm_takes_effect_immediately_and_is_persisted(env, fake_openai):
    client, admin, h = env
    assert client.app.state.container.llm.name == "extractive"
    SEEN.clear()
    r = client.put("/api/settings", headers=admin, json={"llm_provider": "openai_compatible", "openai_base_url": fake_openai,
                                                         "openai_model": "local-model", "openai_api_key": "sk-secret-123456"})
    assert r.status_code == 200 and r.json()["applied"] and r.json()["tests"]["llm"]["ok"] and r.json()["relogin_required"] is False
    chat = client.post("/api/chat", json={"message": "How long does standard shipping take?"}).json()
    assert chat["status"] == "answered" and chat["metrics"]["prompt_tokens"] == 154 and chat["metrics"]["model"] == "local-model"
    assert any(s["path"] == "/v1/chat/completions" and s["auth"] == "Bearer sk-secret-123456" for s in SEEN)  # key reaches the server
    saved = runtime_config.load(h["settings"].runtime_config_path)
    assert saved["llm_provider"] == "openai_compatible" and saved["openai_model"] == "local-model"
    shown = client.get("/api/settings", headers=admin).json()["llm"]
    assert shown["openai_api_key"].startswith("••••") and "secret" not in json.dumps(shown) and shown["provider"] == "openai_compatible"
    # the masked key echoed back by the UI must not overwrite the stored one
    client.put("/api/settings", headers=admin, json={"openai_api_key": shown["openai_api_key"], "openai_model": "local-model"})
    assert client.app.state.container.settings.openai_api_key == "sk-secret-123456"


def test_a_failing_change_leaves_the_running_app_untouched(env, fake_openai):
    client, admin, h = env
    before = client.app.state.container
    for body, needle in [
        ({"llm_provider": "openai_compatible", "openai_base_url": fake_openai, "openai_model": "broken"}, "Language model test failed"),
        ({"llm_provider": "openai_compatible", "openai_base_url": "http://127.0.0.1:9/v1", "openai_model": "m"}, "Language model test failed"),
        ({"ollama_base_url": "not-a-url"}, "http(s) URL"),
        ({"ollama_num_ctx": 5}, "Context window"),
        ({"database_url": "postgresql+psycopg://u:p@127.0.0.1:1/x"}, "Database test failed"),
        ({"llm_provider": "ollama", "ollama_model": "   "}, "Ollama model is required"),
        ({"llm_provider": "openai_compatible", "openai_base_url": "", "openai_model": ""}, "OpenAI-compatible base URL is required"),
    ]:
        r = client.put("/api/settings", json=body, headers=admin)
        assert r.status_code == 422 and needle in r.json()["detail"], (body, r.text)
    assert client.app.state.container is before
    assert runtime_config.load(h["settings"].runtime_config_path).get("llm_provider") is None
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 200  # still serving


def test_switching_database_creates_the_schema_a_login_and_keeps_old_data(env):
    client, admin, h = env
    old_db = client.app.state.container.settings.database_url
    new_url = sqlite_url(h["tmp"], "second.db")
    r = client.put("/api/settings", headers=admin, json={"database_url": new_url, "seed_demo_data": True})
    body = r.json()
    assert r.status_code == 200 and body["database_changed"] and body["relogin_required"] and body["tests"]["database"]["state"] == "empty"
    # the old session is rejected (it was issued by the other database) ...
    assert client.get("/api/settings", headers=admin).status_code == 401
    # ... and the new database has its own admin and its own seeded knowledge base
    new_admin = login(client, "admin@supportpilot.example", "admin-demo-1")
    s = client.get("/api/settings", headers=new_admin).json()
    assert s["database"]["url"].endswith("second.db") and s["knowledge_base"]["documents"] == 7 and s["knowledge_base"]["needs_reindex"] is False
    assert client.post("/api/chat", json={"message": "How long does standard shipping take?"}).json()["citations"]
    # switch back: the first database and its documents are exactly as we left them
    assert client.put("/api/settings", headers=new_admin, json={"database_url": old_db}).status_code == 200
    back = login(client, "admin@supportpilot.example", "admin-demo-1")
    assert client.get("/api/settings", headers=back).json()["knowledge_base"]["documents"] == 7
    assert client.get("/api/orders/ORD-1024", headers=back).status_code == 200


def test_unseeded_new_database_starts_empty_but_usable(env):
    client, admin, h = env
    assert client.put("/api/settings", headers=admin, json={"database_url": sqlite_url(h["tmp"], "empty.db")}).status_code == 200
    a = login(client, "admin@supportpilot.example", "admin-demo-1")
    kb = client.get("/api/settings", headers=a).json()["knowledge_base"]
    assert kb["documents"] == 0 and kb["indexed"] == 0
    assert "couldn't find reliable information" in client.post("/api/chat", json={"message": "What is your refund policy?"}).json()["answer"]


# ---- vector index -----------------------------------------------------------------------------------------------

def test_a_stale_vector_index_is_detected_and_rebuilt_from_the_database(env):
    client, admin, _ = env
    client.app.state.container.store.reset()  # e.g. the vector folder was deleted
    kb = client.get("/api/settings", headers=admin).json()["knowledge_base"]
    assert kb["needs_reindex"] is True and kb["indexed"] == 0 and kb["chunks"] > 20
    done = client.post("/api/settings/reindex", headers=admin).json()
    assert done["chunks"] == kb["chunks"]
    assert client.get("/api/settings", headers=admin).json()["knowledge_base"]["needs_reindex"] is False
    assert client.post("/api/chat", json={"message": "How long does standard shipping take?"}).json()["citations"]


def test_reset_drops_ui_overrides(env, fake_openai, monkeypatch):
    client, admin, h = env
    # "Reset" rebuilds from the process environment / .env. Point that at throwaway locations so this test can never
    # touch a developer's real database or vector store.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(h['tmp'] / 'env_default.db').as_posix()}")
    monkeypatch.setenv("CHROMA_PATH", (h["tmp"] / "env_chroma").as_posix())
    monkeypatch.setenv("LLM_PROVIDER", "extractive")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    client.put("/api/settings", headers=admin, json={"llm_provider": "openai_compatible", "openai_base_url": fake_openai, "openai_model": "local-model"})
    assert runtime_config.load(h["settings"].runtime_config_path)["llm_provider"] == "openai_compatible"
    r = client.post("/api/settings/reset", headers=admin)
    assert r.status_code == 200 and r.json()["relogin_required"] is True  # the environment's database differs from the test one
    assert client.app.state.container.settings.database_url.endswith("env_default.db")
    saved = runtime_config.load(h["settings"].runtime_config_path)
    assert "llm_provider" not in saved and len(saved.get("jwt_secret", "")) >= 0


# ---- client protocol tests --------------------------------------------------------------------------------------

def test_openai_client_protocol(fake_openai):
    SEEN.clear()
    llm = OpenAICompatProvider(fake_openai, "local-model", "sk-1", default_max_tokens=50)
    r = llm.chat([{"role": "user", "content": "hi"}], json_mode=True, temperature=0)
    assert json.loads(r.text)["intent"] == "knowledge_question" and r.prompt_tokens == 77 and r.completion_tokens == 9
    sent = SEEN[-1]
    assert sent["response_format"] == {"type": "json_object"} and sent["max_tokens"] == 50 and sent["auth"] == "Bearer sk-1"
    assert llm.health()["ok"] is True and OpenAICompatProvider(fake_openai, "nope").health()["ok"] is False


def test_openai_embeddings_return_vectors_in_input_order(fake_openai):
    emb = OpenAICompatEmbeddings(fake_openai, "embed-model")
    vecs = emb.embed_documents(["a", "bbb", "cc"])  # the fake answers out of order; the client must re-sort by index
    assert [v[0] for v in vecs] == [1.0, 3.0, 2.0] and emb.embed_query("hello")[0] == 5.0


def test_blank_fields_of_unselected_providers_are_ignored(env, fake_openai):
    """The UI sends every field; blank OpenAI fields must not block testing or saving an Ollama/extractive setup."""
    client, admin, _ = env
    body = {"llm_provider": "extractive", "ollama_base_url": "http://127.0.0.1:11434", "ollama_model": "llama3.2:3b", "ollama_num_ctx": 4096,
            "openai_base_url": "", "openai_model": "", "embedding_provider": "hash", "embedding_model": ""}
    assert client.post("/api/settings/test/llm", json=body, headers=admin).json()["ok"] is True
    assert client.post("/api/settings/test/embedding", json=body, headers=admin).json()["ok"] is True
    assert client.put("/api/settings", json=body, headers=admin).status_code == 200
