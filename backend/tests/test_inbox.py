from tests.helpers import Chat


def escalate(container, session, customer="CUST-1001"):
    chat = Chat(container, session, customer)
    r = chat.say("My payment was successful but my order wasn't created.")
    return chat.conv, r["ticket"]


def test_inbox_requires_admin_key(client, aisha):
    for method, path in [("get", "/api/support/tickets"), ("get", "/api/support/tickets/TCK-00001"),
                         ("patch", "/api/support/tickets/TCK-00001"), ("post", "/api/support/tickets/TCK-00001/notes"),
                         ("post", "/api/support/tickets/TCK-00001/reply")]:
        assert getattr(client, method)(path).status_code == 401, path
        assert getattr(client, method)(path, headers=aisha).status_code == 403, path  # authenticated, but a customer is not an agent


def test_queue_counts_filters_and_ordering(client, admin, container, session):
    _, ref = escalate(container, session)
    q = client.get("/api/support/tickets", headers=admin).json()
    assert set(q["counts"]) == {"open", "in_progress", "resolved"} and sum(q["counts"].values()) >= 45
    items = q["items"]
    group = {"open": 0, "in_progress": 1, "resolved": 2}
    keys = [(group[t["status"]], {"high": 0, "normal": 1, "low": 2}[t["priority"]]) for t in items]
    assert keys == sorted(keys)  # open before in progress before resolved; high priority first inside each group
    assert items[0]["status"] == "open" and items[-1]["status"] == "resolved"
    mine = client.get("/api/support/tickets?priority=high&status=open&q=CUST-1001", headers=admin).json()["items"]
    assert any(t["reference"] == ref for t in mine) and all(t["priority"] == "high" for t in mine)
    assert client.get("/api/support/tickets?status=bogus", headers=admin).status_code == 422


def test_detail_has_transcript_customer_and_activity(client, admin, container, session):
    conv, ref = escalate(container, session)
    d = client.get(f"/api/support/tickets/{ref}", headers=admin).json()
    assert d["customer"]["name"] == "Aisha Khan" and d["conversation_id"] == conv
    assert [m["role"] for m in d["transcript"]] == ["user", "assistant"]
    assert "payment" in d["description"].lower() and d["activity"] == []
    assert client.get("/api/support/tickets/TCK-99999", headers=admin).status_code == 404
    assert client.get("/api/support/tickets/not-a-ref", headers=admin).status_code == 404


def test_assign_moves_open_to_in_progress_and_logs_activity(client, admin, container, session):
    _, ref = escalate(container, session)
    r = client.patch(f"/api/support/tickets/{ref}", json={"assignee": "Priya", "author": "Priya"}, headers=admin).json()
    assert r["assignee"] == "Priya" and r["status"] == "in_progress"
    r = client.patch(f"/api/support/tickets/{ref}", json={"status": "resolved", "author": "Priya"}, headers=admin).json()
    assert r["status"] == "resolved"
    kinds = [a["kind"] for a in client.get(f"/api/support/tickets/{ref}", headers=admin).json()["activity"]]
    assert kinds.count("assign") == 1 and kinds.count("status") >= 2
    assert client.patch(f"/api/support/tickets/{ref}", json={"status": "closed"}, headers=admin).status_code == 422


def test_internal_note_is_never_visible_to_the_customer(client, admin, aisha, container, session):
    conv, ref = escalate(container, session)
    assert client.post(f"/api/support/tickets/{ref}/notes", json={"body": "Checked with payments: charge is pending"}, headers=admin).status_code == 201
    msgs = client.get(f"/api/conversations/{conv}", headers=aisha).json()["messages"]
    assert not any("payments" in m["content"] for m in msgs)
    assert client.post(f"/api/support/tickets/{ref}/notes", json={"body": ""}, headers=admin).status_code == 422


def test_agent_reply_appears_in_customer_conversation(client, admin, aisha, container, session):
    conv, ref = escalate(container, session)
    r = client.post(f"/api/support/tickets/{ref}/reply", json={"body": "Hi Aisha, we found the payment and are creating your order.", "author": "Priya"}, headers=admin)
    assert r.status_code == 201
    msgs = client.get(f"/api/conversations/{conv}", headers=aisha).json()["messages"]
    assert msgs[-1]["role"] == "agent" and "creating your order" in msgs[-1]["content"]
    assert client.get(f"/api/support/tickets/{ref}", headers=admin).json()["status"] == "in_progress"
    # the bot keeps working afterwards and the agent turn is usable as history
    follow = client.post("/api/chat", json={"message": "thanks, where is order ORD-1024?", "conversation_id": conv}, headers=aisha)
    assert follow.status_code == 200 and follow.json()["escalated"] is True


def test_reply_needs_a_conversation(client, admin, session):
    from app.models.db import SupportTicket

    t = SupportTicket(customer_id="CUST-1001", subject="Manual ticket", description="created by phone", conversation_id=None)
    session.add(t)
    session.commit()
    assert client.post(f"/api/support/tickets/{t.reference}/reply", json={"body": "hello"}, headers=admin).status_code == 409
