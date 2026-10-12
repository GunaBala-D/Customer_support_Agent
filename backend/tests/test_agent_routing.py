import pytest

from app.agents.router import classify_rules, extract_order_ids, wants_personal_refund_check
from tests.helpers import Chat


@pytest.mark.parametrize(
    "message, has_memory, expected",
    [
        ("Where is order ORD-1024?", False, "order_status"),
        ("Can you check whether my order has shipped?", False, "order_status"),
        ("Where is it now?", True, "order_status"),
        ("Please cancel my order.", False, "cancellation_request"),
        ("cancel ORD-1001", False, "cancellation_request"),
        ("How do I cancel my order?", False, "knowledge_question"),
        ("What is your cancellation policy?", False, "knowledge_question"),
        ("What is your refund policy?", False, "refund_question"),
        ("My payment was successful but my order wasn't created.", False, "escalation"),
        ("I want to talk to a human", False, "escalation"),
        ("This is unacceptable, the worst service ever", False, "complaint"),
        ("Show me my account details", False, "customer_information"),
        ("and what is in it?", True, "order_status"),
        ("What did I order in ORD-1024?", False, "order_status"),
        ("hello", False, "unknown"),
        ("My package says delivered but I cannot find it", False, "knowledge_question"),
        ("ORD-1024 never arrived", False, "escalation"),
        ("My order number is ORD-1024.", False, "order_status"),
    ],
)
def test_rule_router(message, has_memory, expected):
    intent, reason = classify_rules(message, has_memory)
    assert intent == expected and reason


def test_rules_defer_when_inconclusive():
    assert classify_rules("How long does standard shipping take?") is None


def test_order_id_extraction_is_regex_only():
    assert extract_order_ids("ord-1024 and ORD-1024 and ORD-2000, not ORDER-5") == ["ORD-1024", "ORD-2000"]


def test_personal_vs_policy_refund_question():
    assert not wants_personal_refund_check("What is your refund policy?", has_order=False)
    assert wants_personal_refund_check("Can I return ORD-1002?", has_order=True)
    assert not wants_personal_refund_check("How can I return my product?", has_order=True)


def test_graph_routes_knowledge_vs_tools(container, session):
    chat = Chat(container, session)
    k = chat.say("How long does standard shipping take?")
    assert k["intent"] == "knowledge_question" and not k["tool_calls"] and k["citations"]
    t = Chat(container, session).say("Where is order ORD-1024?")
    assert t["intent"] == "order_status" and [x["tool"] for x in t["tool_calls"]] == ["get_order_status", "get_order_items"]
    assert not t["citations"]  # tool answers never carry knowledge-base citations


def test_policy_refund_question_does_not_call_tools(container, session):
    r = Chat(container, session).say("What is your refund policy?")
    assert r["intent"] == "refund_question" and r["tool_calls"] == [] and r["citations"]


def test_personal_refund_question_uses_tool_and_cites_policy(container, session):
    r = Chat(container, session).say("Can I return ORD-1002?")
    assert [t["tool"] for t in r["tool_calls"]] == ["check_refund_eligibility"]
    assert "30-day return window" in r["answer"] and r["citations"][0]["filename"] == "refund_policy.md"


def test_expired_window_is_reported_not_guessed(container, session):
    r = Chat(container, session).say("Can I return ORD-1003?")
    assert "past the 30-day" in r["answer"] and "support ticket" not in r["answer"]


def test_trace_is_high_level_and_has_no_reasoning_dump(container, session):
    r = Chat(container, session).say("Where is order ORD-1024?")
    steps = [t["step"] for t in r["trace"]]
    assert "router" in steps and steps[-1] == "validation"
    assert all(set(t) == {"step", "detail"} for t in r["trace"])


def test_unknown_intent_gets_menu_then_human_offer(container, session):
    chat = Chat(container, session)
    assert chat.say("hello")["status"] == "clarification"
    chat.say("hi")
    assert "human support agent" in chat.say("hey")["answer"]


def test_anonymous_user_cannot_trigger_order_lookup(container, session):
    r = Chat(container, session, customer=None).say("Where is order ORD-1024?")
    assert r["tool_calls"] == [] and "sign in" in r["answer"]


def test_missing_order_number_is_requested_then_remembered(container, session):
    chat = Chat(container, session)
    r1 = chat.say("Where is my order?")
    assert r1["status"] == "clarification" and r1["tool_calls"] == []
    r2 = chat.say("ORD-1024")
    assert r2["intent"] == "order_status" and "ORD-1024" in r2["answer"]


def test_items_follow_up_needs_an_order_in_memory():
    assert classify_rules("and what is in it?", False) is None  # no order context -> not an order question
    assert classify_rules("What is in the warranty policy?", True) is None
