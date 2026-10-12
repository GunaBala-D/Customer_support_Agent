import pytest

from app.models.db import Order, SupportTicket, ToolCall
from app.tools import ToolContext, run_tool


def ctx(session, customer="CUST-1001", admin=False):
    return ToolContext(session, customer, "conv-test", admin)


def test_order_status_happy_path(session):
    r = run_tool(ctx(session), "get_order_status", {"order_id": "ORD-1024"})
    assert r.success and r.data["status"] == "shipped" and r.data["tracking_number"]


def test_items_tool(session):
    r = run_tool(ctx(session), "get_order_items", {"order_id": "ORD-1001"})
    assert r.success and r.data["items"] and {"sku", "name", "quantity"} <= set(r.data["items"][0])


def test_cannot_read_another_customers_order(session):
    r = run_tool(ctx(session), "get_order_status", {"order_id": "ORD-1004"})  # belongs to CUST-1002
    assert not r.success and r.error_code == "not_found"
    assert "CUST-1002" not in (r.error or "")  # no data leak in the message


def test_anonymous_cannot_use_order_tools(session):
    r = run_tool(ctx(session, customer=None), "get_order_status", {"order_id": "ORD-1001"})
    assert r.error_code == "auth_required"


def test_admin_can_read_any_order(session):
    assert run_tool(ctx(session, None, admin=True), "get_order_status", {"order_id": "ORD-1004"}).success


@pytest.mark.parametrize("bad", ["1001", "ORD-1; DROP", "ord-1001", "ORD-", "", None, 5])
def test_order_id_validation(session, bad):
    r = run_tool(ctx(session), "get_order_status", {"order_id": bad})
    assert not r.success and r.error_code == "invalid_arguments"


def test_customer_details_self_only(session):
    assert run_tool(ctx(session), "get_customer_details", {"customer_id": "CUST-1001"}).success
    r = run_tool(ctx(session), "get_customer_details", {"customer_id": "CUST-1002"})
    assert r.error_code == "forbidden"
    data = run_tool(ctx(session), "get_customer_details", {"customer_id": "CUST-1001"}).data
    assert data["phone_masked"].startswith("***")


def test_unknown_tool_and_bad_args(session):
    assert run_tool(ctx(session), "drop_database", {}).error_code == "unknown_tool"
    assert run_tool(ctx(session), "get_order_status", {"nope": 1}).error_code == "invalid_arguments"


def test_refund_eligibility_cases(session):
    ok = run_tool(ctx(session), "check_refund_eligibility", {"order_id": "ORD-1002"}).data
    assert ok["eligible"] and ok["reason"] == "within_window" and ok["days_since_delivery"] == 10
    late = run_tool(ctx(session), "check_refund_eligibility", {"order_id": "ORD-1003"}).data
    assert not late["eligible"] and late["reason"] == "window_expired"
    shipped = run_tool(ctx(session), "check_refund_eligibility", {"order_id": "ORD-1024"}).data
    assert shipped["reason"] == "not_delivered"


def test_cancel_requires_confirmation(session):
    r = run_tool(ctx(session), "cancel_order", {"order_id": "ORD-1001"})  # confirmed defaults to False
    assert not r.success and r.error_code == "confirmation_required"
    assert session.get(Order, "ORD-1001").status == "processing"


def test_cancel_after_confirmation_and_rules(session):
    r = run_tool(ctx(session), "cancel_order", {"order_id": "ORD-1001"}, confirmed=True)
    assert r.success and r.data["status"] == "cancelled"
    assert session.get(Order, "ORD-1001").payment_status == "refund_pending"
    assert run_tool(ctx(session), "cancel_order", {"order_id": "ORD-1001"}, confirmed=True).error_code == "already_cancelled"
    assert run_tool(ctx(session), "cancel_order", {"order_id": "ORD-1024"}, confirmed=True).error_code == "not_cancellable"


def test_cannot_cancel_someone_elses_order_even_when_confirmed(session):
    r = run_tool(ctx(session), "cancel_order", {"order_id": "ORD-1004"}, confirmed=True)
    assert r.error_code == "not_found" and session.get(Order, "ORD-1004").status == "processing"


def test_create_ticket(session):
    r = run_tool(ctx(session), "create_support_ticket",
                 {"subject": "Broken item", "description": "It arrived broken", "category": "order",
                  "priority": "high", "order_id": "ORD-1002"})
    assert r.success and r.data["reference"].startswith("TCK-")
    t = session.get(SupportTicket, r.data["ticket_id"])
    assert t.customer_id == "CUST-1001" and t.order_id == "ORD-1002" and t.conversation_id == "conv-test"
    bad_cat = run_tool(ctx(session), "create_support_ticket", {"subject": "x", "description": "y", "category": "bogus"})
    assert bad_cat.error_code == "invalid_arguments"
    foreign = run_tool(ctx(session), "create_support_ticket", {"subject": "x", "description": "y", "order_id": "ORD-1004"})
    assert foreign.error_code == "not_found"


def test_every_call_is_audited(session):
    before = session.query(ToolCall).count()
    run_tool(ctx(session), "get_order_status", {"order_id": "ORD-1024"})
    run_tool(ctx(session), "get_order_status", {"order_id": "ORD-1004"})
    rows = session.query(ToolCall).order_by(ToolCall.id.desc()).limit(2).all()
    assert session.query(ToolCall).count() == before + 2
    assert [r.success for r in rows] == [False, True] and all(r.duration_ms >= 0 for r in rows)
