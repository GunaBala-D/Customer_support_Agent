"""Multi-turn flows that exercise routing + RAG + tools + memory + escalation together."""
from app.models.db import Conversation, SupportTicket
from app.services.cache import bump_kb_version, kb_version
from tests.helpers import Chat


def test_full_customer_journey(container, session):
    chat = Chat(container, session)
    q = chat.say("What is your refund policy?")
    assert q["status"] == "answered" and q["citations"]
    s = chat.say("My order number is ORD-1024.")
    assert s["intent"] == "order_status" and "shipped" in s["answer"]
    follow = chat.say("Where is it now?")
    assert "ORD-1024" in follow["answer"]  # pronoun resolved from memory
    assert session.get(Conversation, chat.conv).status == "resolved"


def test_payment_problem_escalates_with_ticket(container, session):
    chat = Chat(container, session)
    r = chat.say("My payment was successful but my order wasn't created.")
    assert r["escalated"] and r["status"] == "escalated" and r["ticket"].startswith("TCK-")
    ticket = session.get(SupportTicket, int(r["ticket"].split("-")[1]))
    assert ticket.category == "payment" and ticket.priority == "high" and ticket.customer_id == "CUST-1001"
    assert ticket.conversation_id == chat.conv and "payment" in ticket.description.lower()
    assert session.get(Conversation, chat.conv).status == "escalated"
    again = chat.say("I want to talk to a human")  # no duplicate tickets
    assert r["ticket"] in again["answer"]
    assert session.query(SupportTicket).filter_by(conversation_id=chat.conv).count() == 1


def test_unanswerable_question_offers_human_and_yes_escalates(container, session):
    chat = Chat(container, session)
    r = chat.say("Who won the football world cup in 2018?")
    assert r["status"] == "fallback" and r["citations"] == [] and "couldn't find reliable information" in r["answer"]
    handoff = chat.say("yes")
    assert handoff["escalated"] and handoff["ticket"]


def test_complaint_escalates(container, session):
    r = Chat(container, session).say("This is unacceptable, I am furious about the service")
    assert r["escalated"] and r["answer"].startswith("I'm sorry")


def test_customer_information_is_scoped_to_signed_in_user(container, session):
    r = Chat(container, session, "CUST-1002").say("Show me my account details")
    assert "Daniel Martinez" in r["answer"] and "aisha" not in r["answer"].lower()
    assert [t["tool"] for t in r["tool_calls"]] == ["get_customer_details"]


def test_knowledge_answer_cache_and_invalidation(container, session):
    q = "How long does standard shipping take?"
    first = Chat(container, session).say(q)
    second = Chat(container, session).say(q)
    assert first["metrics"]["cache_hit"] is False and second["metrics"]["cache_hit"] is True
    assert second["citations"] == first["citations"]
    before = kb_version(container.cache)
    bump_kb_version(container.cache)  # what an upload/delete does
    assert kb_version(container.cache) == before + 1
    third = Chat(container, session).say(q)
    assert third["metrics"]["cache_hit"] is False


def test_customer_data_is_never_cached(container, session):
    Chat(container, session).say("Where is order ORD-1024?")
    cache = container.cache
    assert not any("ORD-" in k or "CUST-" in k for k in getattr(cache, "_data", {}))


def test_follow_up_question_borrows_previous_topic(container, session):
    chat = Chat(container, session)
    chat.say("How long does standard shipping take?")
    r = chat.say("And express?")
    assert r["status"] == "answered" and r["citations"][0]["filename"] == "shipping_policy.pdf"


def test_first_start_without_the_model_server_still_boots(tmp_path):
    """A new user often starts the app before Ollama is up. AUTO_SEED must degrade to a warning, not a crash."""
    from fastapi.testclient import TestClient

    from app.config.settings import REPO_ROOT, Settings
    from app.main import create_app
    from tests.conftest import JWT_SECRET, login

    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'boot.db').as_posix()}", chroma_path=(tmp_path / "chroma").as_posix(),
        documents_dir=(REPO_ROOT / "data" / "documents").as_posix(), llm_provider="extractive", embedding_provider="ollama",
        ollama_base_url="http://127.0.0.1:9", auto_seed=True, jwt_secret=JWT_SECRET, app_env="test", redis_url="",
        runtime_config_path=(tmp_path / "runtime.json").as_posix(),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        admin = login(client, "admin@supportpilot.example", "admin-demo-1")  # users exist even though indexing failed
        kb = client.get("/api/settings", headers=admin).json()["knowledge_base"]
        assert kb["documents"] == 0 and kb["indexed"] == 0
        # a chat still answers (with the honest fallback) instead of erroring
        assert client.post("/api/chat", json={"message": "hello"}).status_code == 200
