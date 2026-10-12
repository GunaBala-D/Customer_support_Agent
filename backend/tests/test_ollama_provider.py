"""Protocol tests for the Ollama clients using a fake server that implements the documented
Ollama HTTP API (/api/chat, /api/embed, /api/tags). This verifies request shape, response
parsing and error handling; it does NOT test model quality (that needs a real Ollama)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.rag.embeddings import EmbeddingError, OllamaEmbeddings
from app.services.llm.base import LLMError
from app.services.llm.ollama import OllamaProvider

SEEN: list[dict] = []


class FakeOllama(BaseHTTPRequestHandler):
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
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": "llama3.2:3b"}, {"name": "nomic-embed-text:latest"}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, **body})
        if self.path == "/api/chat":
            if body["model"] == "broken":
                return self._send(500, {"error": "model crashed"})
            reply = '{"intent": "knowledge_question"}' if body.get("format") == "json" else "Standard shipping takes 3 to 5 business days [1]."
            return self._send(200, {"message": {"role": "assistant", "content": reply}, "prompt_eval_count": 120, "eval_count": 14})
        if self.path == "/api/embed":
            return self._send(200, {"embeddings": [[float(len(t)), 1.0, 0.0] for t in body["input"]]})
        self._send(404, {})


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_chat_request_shape_and_token_accounting(server):
    llm = OllamaProvider(server, "llama3.2:3b", num_ctx=2048, default_max_tokens=100)
    SEEN.clear()
    r = llm.chat([{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=50)
    sent = SEEN[-1]
    assert sent["path"] == "/api/chat" and sent["model"] == "llama3.2:3b" and sent["stream"] is False
    assert sent["options"] == {"temperature": 0.0, "num_predict": 50, "num_ctx": 2048}
    assert r.text.startswith("Standard shipping") and r.prompt_tokens == 120 and r.completion_tokens == 14
    assert r.latency_ms >= 0 and r.model == "llama3.2:3b"


def test_json_mode_sets_format(server):
    llm = OllamaProvider(server, "llama3.2:3b")
    SEEN.clear()
    r = llm.chat([{"role": "user", "content": "x"}], json_mode=True)
    assert SEEN[-1]["format"] == "json" and json.loads(r.text)["intent"] == "knowledge_question"


def test_server_error_and_unreachable_raise_llm_error(server):
    with pytest.raises(LLMError):
        OllamaProvider(server, "broken").chat([{"role": "user", "content": "x"}])
    with pytest.raises(LLMError):
        OllamaProvider("http://127.0.0.1:9", "m", timeout=1).chat([{"role": "user", "content": "x"}])


def test_health_reports_missing_model_with_fix(server):
    assert OllamaProvider(server, "llama3.2:3b").health()["ok"] is True
    h = OllamaProvider(server, "qwen2.5:7b").health()
    assert h["ok"] is False and "ollama pull qwen2.5:7b" in h["error"]
    assert OllamaProvider("http://127.0.0.1:9", "m").health()["ok"] is False


def test_nomic_prefixes_are_applied(server):
    emb = OllamaEmbeddings(server, "nomic-embed-text")
    SEEN.clear()
    docs = emb.embed_documents(["a", "bb"])
    assert SEEN[-1]["input"] == ["search_document: a", "search_document: bb"] and len(docs) == 2
    emb.embed_query("q")
    assert SEEN[-1]["input"] == ["search_query: q"]
    other = OllamaEmbeddings(server, "mxbai-embed-large")
    other.embed_query("q")
    assert SEEN[-1]["input"] == ["q"]


def test_embedding_failure_is_explicit():
    with pytest.raises(EmbeddingError):
        OllamaEmbeddings("http://127.0.0.1:9", "nomic-embed-text", timeout=1).embed_query("x")


def test_full_agent_runs_on_the_ollama_provider(server, tmp_path):
    """The whole pipeline wired to OllamaProvider (fake server) instead of the test double."""
    from app.config.settings import REPO_ROOT, Settings
    from app.services.bootstrap import bootstrap
    from app.services.container import build_container
    from app.services.orchestrator import handle_chat

    s = Settings(database_url=f"sqlite:///{(tmp_path / 'o.db').as_posix()}", chroma_path=(tmp_path / "c").as_posix(),
                 documents_dir=(REPO_ROOT / "data" / "documents").as_posix(), llm_provider="ollama",
                 embedding_provider="hash", ollama_base_url=server, ollama_model="llama3.2:3b", redis_url="")
    c = build_container(s)
    assert c.llm.name == "ollama"
    bootstrap(c, seed=True)
    with c.session_factory() as session:
        r = handle_chat(c, session, message="How long does standard shipping take?", conversation_id=None, customer_id=None)
        assert r["status"] == "answered" and r["citations"][0]["filename"] == "shipping_policy.pdf"
        assert r["metrics"]["prompt_tokens"] == 240 and r["metrics"]["model"] == "llama3.2:3b"
        # 2 LLM calls (rules were inconclusive -> classification, then the grounded answer): 2 x 120 tokens
        assert any(e["path"] == "/api/chat" for e in SEEN)
