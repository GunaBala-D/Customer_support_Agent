from app.models.db import Conversation, Order, ToolCall
from tests.helpers import Chat


def status(session, oid="ORD-1001"):
    session.expire_all()
    return session.get(Order, oid).status


def cancel_calls(session):
    return session.query(ToolCall).filter(ToolCall.tool_name == "cancel_order").all()


def test_cancel_asks_before_acting(container, session):
    chat = Chat(container, session)
    r = chat.say("Please cancel order ORD-1001")
    assert r["status"] == "needs_confirmation" and "proceed with the cancellation" in r["answer"]
    assert "cancel_order" not in [t["tool"] for t in r["tool_calls"]]
    assert status(session) == "processing"
    assert session.get(Conversation, chat.conv).state["pending_action"]["order_id"] == "ORD-1001"


def test_yes_executes_exactly_once(container, session):
    chat = Chat(container, session)
    chat.say("Cancel order ORD-1001")
    before = len(cancel_calls(session))
    r = chat.say("yes")
    assert r["status"] == "completed" and "has been cancelled" in r["answer"]
    assert status(session) == "cancelled" and len(cancel_calls(session)) == before + 1
    assert cancel_calls(session)[-1].confirmed is True
    again = chat.say("yes")  # a stray 'yes' must not repeat or re-run the action
    assert len(cancel_calls(session)) == before + 1 and again["status"] != "completed"


def test_no_declines_and_changes_nothing(container, session):
    chat = Chat(container, session)
    chat.say("Cancel order ORD-1001")
    r = chat.say("no")
    assert "left everything as it is" in r["answer"] and status(session) == "processing"
    assert chat.say("yes")["status"] != "completed"  # pending action was cleared


def test_unrelated_message_discards_pending_action(container, session):
    chat = Chat(container, session)
    chat.say("Cancel order ORD-1001")
    chat.say("How long does standard shipping take?")
    chat.say("yes")
    assert status(session) == "processing"


def test_confirmation_does_not_leak_across_conversations(container, session):
    Chat(container, session).say("Cancel order ORD-1001")
    other = Chat(container, session)  # new conversation, no pending action
    other.say("yes")
    assert status(session) == "processing"


def test_shipped_order_cannot_be_cancelled_and_no_confirmation_requested(container, session):
    r = Chat(container, session).say("Cancel ORD-1024")
    assert r["status"] != "needs_confirmation" and "can no longer be cancelled" in r["answer"]


def test_cannot_cancel_another_customers_order(container, session):
    chat = Chat(container, session)
    r = chat.say("Cancel ORD-1004")
    assert r["status"] != "needs_confirmation" and "couldn't find order ORD-1004" in r["answer"]
    chat.say("yes")
    assert status(session, "ORD-1004") == "processing"


def test_order_from_memory_is_used_for_cancellation(container, session):
    chat = Chat(container, session)
    chat.say("My order number is ORD-1001")
    r = chat.say("Please cancel it")
    assert r["status"] == "needs_confirmation" and "ORD-1001" in r["answer"]


def test_rest_endpoint_enforces_same_gate(client, aisha):
    assert client.post("/api/orders/ORD-1001/cancel", json={}, headers=aisha).status_code == 409
    assert client.post("/api/orders/ORD-1001/cancel", json={"confirm": False}, headers=aisha).status_code == 409
    ok = client.post("/api/orders/ORD-1001/cancel", json={"confirm": True}, headers=aisha)
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled"
