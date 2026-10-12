import io

from docx import Document as Docx

from app.models.db import ToolCall
from tests.conftest import ADMIN_KEY
from tests.pdfwriter import write_pdf


def test_health(client):
    r = client.get("/api/health")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert body["vector_store"]["chunks"] > 10 and body["cache"]["backend"] == "memory"
    assert "X-Request-ID" in r.headers


def test_openapi_docs_available(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/api/chat", "/api/documents/upload", "/api/conversations", "/api/orders/{order_id}",
              "/api/orders/{order_id}/cancel", "/api/support/tickets", "/api/metrics", "/api/health"]:
        assert p in paths


def test_chat_knowledge_question_returns_citations_and_trace(client):
    r = client.post("/api/chat", json={"message": "How long does standard shipping take?"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered"
    assert body["citations"][0]["label"] == "shipping_policy.pdf — page 1"
    assert body["metrics"]["retrieved_chunks"] >= 1 and body["trace"]
    assert "chain" not in str(body).lower()


def test_chat_with_customer_uses_tools_and_keeps_conversation(client, aisha):
    first = client.post("/api/chat", json={"message": "My order number is ORD-1024."}, headers=aisha).json()
    second = client.post("/api/chat", json={"message": "Where is it now?", "conversation_id": first["conversation_id"]},
                         headers=aisha).json()
    assert second["conversation_id"] == first["conversation_id"] and "ORD-1024" in second["answer"]
    assert second["tool_calls"][0]["tool"] == "get_order_status"


def test_chat_input_validation(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi", "conversation_id": "not-a-uuid"}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi"}, headers={"X-Customer-Id": "CUST-9999"}).status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}, headers={"X-Customer-Id": "x' OR 1=1--"}).status_code == 401


def test_conversation_access_control(client, aisha, admin):
    conv = client.post("/api/chat", json={"message": "Where is order ORD-1024?"}, headers=aisha).json()["conversation_id"]
    assert client.get(f"/api/conversations/{conv}", headers=aisha).status_code == 200
    assert client.get(f"/api/conversations/{conv}", headers={"X-Customer-Id": "CUST-1002"}).status_code == 404
    assert client.get(f"/api/conversations/{conv}").status_code == 401
    # another customer cannot continue someone else's conversation
    r = client.post("/api/chat", json={"message": "hi", "conversation_id": conv}, headers={"X-Customer-Id": "CUST-1002"})
    assert r.status_code == 403
    detail = client.get(f"/api/conversations/{conv}", headers=admin).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] and detail["messages"][1]["trace"]
    mine = client.get("/api/conversations", headers=aisha).json()
    assert all(c["customer_id"] == "CUST-1001" for c in mine) and any(c["id"] == conv for c in mine)


def test_admin_endpoints_require_key(client, admin):
    for path in ["/api/metrics", "/api/documents", "/api/events", "/api/evaluation/latest", "/api/support/tickets"]:
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401, path
    assert client.get("/api/metrics", headers=admin).status_code == 200


def test_api_key_can_be_switched_off(settings, container):
    from fastapi.testclient import TestClient

    from app.main import create_app

    off = container.settings.model_copy(update={"allow_api_key": False})
    container.settings, original = off, container.settings
    try:
        with TestClient(create_app(settings, container)) as c:
            assert c.get("/api/metrics", headers={"X-API-Key": ADMIN_KEY}).status_code == 401
            assert c.get("/api/metrics").status_code == 401
    finally:
        container.settings = original


def test_order_endpoints_enforce_ownership(client, aisha, admin):
    ok = client.get("/api/orders/ORD-1024", headers=aisha)
    assert ok.status_code == 200 and ok.json()["items"]
    assert client.get("/api/orders/ORD-1004", headers=aisha).status_code == 404
    assert client.get("/api/orders/ORD-1004", headers=admin).status_code == 200
    assert client.get("/api/orders/ORD-1024").status_code == 401
    assert client.get("/api/orders/ORD-1024'%20OR%201=1", headers=aisha).status_code == 422


def test_ticket_creation(client, aisha):
    r = client.post("/api/support/tickets", headers=aisha,
                    json={"subject": "Damaged box", "description": "The box was crushed.", "category": "order", "order_id": "ORD-1002"})
    assert r.status_code == 201 and r.json()["reference"].startswith("TCK-")
    assert client.post("/api/support/tickets", headers=aisha, json={"subject": "x", "description": "y"}).status_code == 422
    assert client.post("/api/support/tickets", json={"subject": "abc", "description": "abc"}).status_code == 401


def test_document_upload_search_and_delete(client, admin, tmp_path):
    files = {"file": ("loyalty_program.txt", io.BytesIO(b"Loyalty members earn 3 points per dollar. Points never expire while the account is active."), "text/plain")}
    up = client.post("/api/documents/upload", files=files, headers=admin)
    assert up.status_code == 201 and up.json()["chunks"] >= 1
    answer = client.post("/api/chat", json={"message": "How many points do loyalty members earn per dollar?"}).json()
    assert answer["status"] == "answered" and answer["citations"][0]["filename"] == "loyalty_program.txt"
    assert any(d["filename"] == "loyalty_program.txt" for d in client.get("/api/documents", headers=admin).json())
    assert client.delete(f"/api/documents/{up.json()['id']}", headers=admin).status_code == 204
    after = client.post("/api/chat", json={"message": "How many points do loyalty members earn per dollar?"}).json()
    assert all(c["filename"] != "loyalty_program.txt" for c in after["citations"])


def test_upload_pdf_and_docx_and_rejections(client, admin, tmp_path):
    write_pdf(tmp_path / "gift.pdf", [["Gift cards never expire."], ["Gift cards cannot be refunded for cash."]])
    pdf = client.post("/api/documents/upload", files={"file": ("gift_cards.pdf", (tmp_path / "gift.pdf").read_bytes())}, headers=admin)
    assert pdf.status_code == 201 and pdf.json()["pages"] == 2
    buf = io.BytesIO()
    d = Docx()
    d.add_paragraph("Bulk orders above 50 units get a custom quote.")
    d.save(buf)
    assert client.post("/api/documents/upload", files={"file": ("bulk.docx", buf.getvalue())}, headers=admin).status_code == 201
    assert client.post("/api/documents/upload", files={"file": ("evil.exe", b"MZ")}, headers=admin).status_code == 415
    assert client.post("/api/documents/upload", files={"file": ("empty.txt", b"   ")}, headers=admin).status_code == 422
    assert client.post("/api/documents/upload", files={"file": ("../../etc/passwd.txt", b"Path traversal attempt text.")}, headers=admin).json()["filename"] == "passwd.txt"
    assert client.post("/api/documents/upload", files={"file": ("a.txt", b"x")}).status_code == 401


def test_product_endpoint_is_cached(client, session):
    from app.models.db import Product

    sku = session.query(Product).first().sku
    assert client.get(f"/api/products/{sku}").json()["cached"] is False
    assert client.get(f"/api/products/{sku}").json()["cached"] is True
    assert client.get("/api/products/NOPE").status_code == 404


def test_rate_limit(settings, container):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings.model_copy(update={"rate_limit_per_minute": 3}), container)
    with TestClient(app) as c:
        codes = [c.post("/api/chat", json={"message": "hello"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def test_metrics_reflect_activity(client, admin, aisha, session):
    client.post("/api/chat", json={"message": "Where is order ORD-1024?"}, headers=aisha)
    client.post("/api/chat", json={"message": "How long does standard shipping take?"})
    client.post("/api/chat", json={"message": "Who won the world cup?"})
    m = client.get("/api/metrics", headers=admin).json()
    assert m["conversations"]["total"] >= 3 and m["latency_ms"]["avg"] is not None
    assert m["tools"]["total_calls"] >= 1 and 0 <= m["tools"]["success_rate"] <= 1
    assert m["rag"]["queries"] >= 2 and any("world cup" in f["question"].lower() for f in m["failed_queries"])
    assert len(m["conversations_per_day"]) == 14
    events = client.get("/api/events?name=chat_response", headers=admin).json()
    assert events and {"latency_ms", "tools", "model", "status"} <= set(events[0]["payload"])
    assert session.query(ToolCall).count() >= 1
