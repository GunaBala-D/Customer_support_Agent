from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.db import User
from app.services.security import create_token, hash_password, verify_password
from tests.conftest import DEMO_PASSWORD, JWT_SECRET, login


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- password hashing -------------------------------------------------------------------------------------------

def test_password_hash_is_salted_and_verifiable():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b and a.startswith("scrypt$")  # random salt per hash
    assert verify_password("correct horse", a) and not verify_password("wrong", a)
    assert not verify_password("x", "garbage") and not verify_password("x", "md5$1$2$3$4$5")


# ---- login ------------------------------------------------------------------------------------------------------

def test_login_returns_token_and_user(client):
    r = client.post("/api/auth/login", json={"email": "Aisha.Khan@example.com", "password": DEMO_PASSWORD})  # case-insensitive e-mail
    body = r.json()
    assert r.status_code == 200 and body["token_type"] == "bearer" and body["expires_in"] == 3600
    assert body["user"]["role"] == "customer" and body["user"]["customer_id"] == "CUST-1001"
    assert "password" not in str(body)
    claims = jwt.decode(body["access_token"], JWT_SECRET, algorithms=["HS256"], issuer="support-agent")
    assert claims["role"] == "customer" and "email" not in claims  # no personal data inside the token


def test_bad_credentials_give_one_generic_error(client):
    wrong_pw = client.post("/api/auth/login", json={"email": "aisha.khan@example.com", "password": "nope"})
    no_user = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()  # cannot tell "unknown user" from "wrong password"


def test_login_is_rate_limited_per_ip(settings, container):
    with TestClient(create_app(settings, container)) as c:
        codes = [c.post("/api/auth/login", json={"email": "x@y.io", "password": "bad"}).status_code for _ in range(13)]
    assert codes[:10] == [401] * 10 and set(codes[10:]) == {429}  # 10/min brute-force budget


def test_me_and_roles(client, customer_jwt, agent_jwt, admin_jwt):
    assert client.get("/api/auth/me", headers=customer_jwt).json()["role"] == "customer"
    assert client.get("/api/auth/me", headers=agent_jwt).json()["name"] == "Priya Agent"
    assert client.get("/api/auth/me", headers=admin_jwt).json()["role"] == "admin"
    assert client.get("/api/auth/me").status_code == 401


# ---- token validation -------------------------------------------------------------------------------------------

def test_tampered_expired_and_foreign_tokens_are_rejected(client, session, container):
    uid = session.query(User).filter_by(email="aisha.khan@example.com").one().id
    db = container.db_fingerprint
    good = create_token(JWT_SECRET, user_id=uid, role="customer", expires_minutes=5, db=db)
    assert client.get("/api/auth/me", headers=bearer(good)).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(good[:-3] + "abc")).status_code == 401  # signature broken
    assert client.get("/api/auth/me", headers=bearer(create_token(JWT_SECRET, user_id=uid, role="customer", expires_minutes=-5, db=db))).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(create_token("another-secret-another-secret-123456", user_id=uid, role="customer", expires_minutes=5, db=db))).status_code == 401
    none_alg = jwt.encode({"sub": str(uid), "iss": "support-agent", "exp": datetime.now(UTC) + timedelta(minutes=5)}, "", algorithm="none")
    assert client.get("/api/auth/me", headers=bearer(none_alg)).status_code == 401  # alg=none attack
    assert client.get("/api/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer"}).status_code == 401


def test_role_in_the_token_cannot_be_forged(client, session, container):
    """The role is read from the database, so a customer cannot mint an admin token claim."""
    uid = session.query(User).filter_by(email="aisha.khan@example.com").one().id
    forged = create_token(JWT_SECRET, user_id=uid, role="admin", expires_minutes=5, db=container.db_fingerprint)
    assert client.get("/api/metrics", headers=bearer(forged)).status_code == 403


def test_disabled_user_is_locked_out_immediately(client, admin_jwt, session):
    h = login(client, "aisha.khan@example.com", DEMO_PASSWORD)
    uid = session.query(User).filter_by(email="aisha.khan@example.com").one().id
    assert client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=admin_jwt).status_code == 200
    try:
        assert client.get("/api/auth/me", headers=h).status_code == 401  # existing token stops working
        assert client.post("/api/auth/login", json={"email": "aisha.khan@example.com", "password": DEMO_PASSWORD}).status_code == 401
    finally:
        client.patch(f"/api/users/{uid}", json={"is_active": True}, headers=admin_jwt)


# ---- authorisation by role --------------------------------------------------------------------------------------

def test_customer_jwt_scopes_chat_and_orders_to_that_customer(client, customer_jwt):
    mine = client.post("/api/chat", json={"message": "Where is order ORD-1024?"}, headers=customer_jwt).json()
    assert [t["tool"] for t in mine["tool_calls"]] == ["get_order_status", "get_order_items"]
    other = client.post("/api/chat", json={"message": "Where is order ORD-1004?"}, headers=customer_jwt).json()
    assert "couldn't find order" in other["answer"]  # CUST-1002's order
    assert client.get("/api/orders/ORD-1024", headers=customer_jwt).status_code == 200
    assert client.get("/api/orders/ORD-1004", headers=customer_jwt).status_code == 404


def test_staff_see_everything_customers_see_nothing_admin(client, customer_jwt, agent_jwt, admin_jwt):
    for path in ["/api/metrics", "/api/documents", "/api/events", "/api/users"]:
        assert client.get(path, headers=customer_jwt).status_code == 403, path
        assert client.get(path, headers=agent_jwt).status_code == 403, path  # agents work tickets, not settings
        assert client.get(path, headers=admin_jwt).status_code == 200, path
    assert client.get("/api/support/tickets", headers=agent_jwt).status_code == 200
    assert client.get("/api/support/tickets", headers=customer_jwt).status_code == 403
    assert client.get("/api/orders/ORD-1004", headers=agent_jwt).status_code == 200


def test_staff_chat_as_guests_without_account_tools(client, admin_jwt):
    r = client.post("/api/chat", json={"message": "Where is order ORD-1024?"}, headers=admin_jwt).json()
    assert r["tool_calls"] == [] and "sign in" in r["answer"]


def test_agent_identity_comes_from_the_token_not_the_request(client, agent_jwt, container, session):
    from tests.helpers import Chat

    chat = Chat(container, session)
    ref = chat.say("My payment was successful but my order wasn't created.")["ticket"]
    client.post(f"/api/support/tickets/{ref}/notes", json={"body": "looking", "author": "Someone Else"}, headers=agent_jwt)
    activity = client.get(f"/api/support/tickets/{ref}", headers=agent_jwt).json()["activity"]
    assert activity[-1]["author"] == "Priya Agent"  # impersonation attempt ignored


# ---- user management --------------------------------------------------------------------------------------------

def test_admin_creates_users_with_validation(client, admin_jwt):
    ok = client.post("/api/users", json={"email": "New.Agent@Example.com", "name": "New Agent", "password": "a-long-password", "role": "agent"}, headers=admin_jwt)
    assert ok.status_code == 201 and ok.json()["email"] == "new.agent@example.com"
    login(client, "new.agent@example.com", "a-long-password")
    dup = client.post("/api/users", json={"email": "new.agent@example.com", "name": "x", "password": "a-long-password", "role": "agent"}, headers=admin_jwt)
    assert dup.status_code == 409
    short = client.post("/api/users", json={"email": "s@x.io", "name": "x", "password": "short", "role": "agent"}, headers=admin_jwt)
    assert short.status_code == 422
    bad_mail = client.post("/api/users", json={"email": "not-an-email", "name": "x", "password": "a-long-password", "role": "agent"}, headers=admin_jwt)
    assert bad_mail.status_code == 422
    no_link = client.post("/api/users", json={"email": "c@x.io", "name": "x", "password": "a-long-password", "role": "customer"}, headers=admin_jwt)
    assert no_link.status_code == 422  # a customer login must point at a customer record
    stray = client.post("/api/users", json={"email": "d@x.io", "name": "x", "password": "a-long-password", "role": "agent", "customer_id": "CUST-1001"}, headers=admin_jwt)
    assert stray.status_code == 422


def test_last_admin_cannot_be_locked_out(client, admin_jwt, session):
    me = client.get("/api/auth/me", headers=admin_jwt).json()
    assert client.patch(f"/api/users/{me['id']}", json={"is_active": False}, headers=admin_jwt).status_code == 409
    assert client.patch(f"/api/users/{me['id']}", json={"role": "agent"}, headers=admin_jwt).status_code == 409


def test_change_password(client):
    email = "aisha.khan@example.com"
    h = login(client, email, DEMO_PASSWORD)
    assert client.post("/api/auth/change-password", json={"current_password": "wrong", "new_password": "brand-new-pass"}, headers=h).status_code == 403
    assert client.post("/api/auth/change-password", json={"current_password": DEMO_PASSWORD, "new_password": "short"}, headers=h).status_code == 422
    assert client.post("/api/auth/change-password", json={"current_password": DEMO_PASSWORD, "new_password": "brand-new-pass"}, headers=h).status_code == 200
    try:
        assert client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}).status_code == 401
        login(client, email, "brand-new-pass")
    finally:
        h2 = login(client, email, "brand-new-pass")
        client.post("/api/auth/change-password", json={"current_password": "brand-new-pass", "new_password": DEMO_PASSWORD}, headers=h2)


# ---- legacy credentials & production hardening ------------------------------------------------------------------

def test_demo_header_and_demo_accounts_are_off_in_production(settings, container):
    prod = container.settings.model_copy(update={"app_env": "production"})
    container.settings, original = prod, container.settings
    try:
        with TestClient(create_app(settings, container)) as c:
            assert c.post("/api/chat", json={"message": "hi"}, headers={"X-Customer-Id": "CUST-1001"}).status_code == 200
            r = c.post("/api/chat", json={"message": "Where is order ORD-1024?"}, headers={"X-Customer-Id": "CUST-1001"}).json()
            assert r["tool_calls"] == []  # header ignored: still a guest
            assert c.get("/api/demo/accounts").status_code == 404
    finally:
        container.settings = original


def test_production_refuses_to_start_without_a_strong_jwt_secret():
    from app.config.settings import Settings
    from app.services.runtime_config import ensure_jwt_secret

    with pytest.raises(RuntimeError):
        ensure_jwt_secret(Settings(app_env="production", jwt_secret=""))
    with pytest.raises(RuntimeError):
        ensure_jwt_secret(Settings(app_env="production", jwt_secret="too-short"))


def test_dev_jwt_secret_is_generated_once_and_persisted(tmp_path):
    from app.config.settings import Settings
    from app.services.runtime_config import ensure_jwt_secret

    s = Settings(app_env="development", jwt_secret="", runtime_config_path=(tmp_path / "rt.json").as_posix())
    first = ensure_jwt_secret(s).jwt_secret
    assert len(first) >= 32 and ensure_jwt_secret(s).jwt_secret == first  # stable across restarts


def test_demo_accounts_endpoint_in_development(client):
    accounts = client.get("/api/demo/accounts").json()
    assert {"admin", "agent", "customer"} <= {a["role"] for a in accounts}


def test_token_from_another_database_is_rejected(client, session, container):
    """After switching databases, user id 1 may be a different person: such tokens must not carry over."""
    uid = session.query(User).filter_by(email="aisha.khan@example.com").one().id
    foreign = create_token(JWT_SECRET, user_id=uid, role="customer", expires_minutes=5, db="some-other-db")
    r = client.get("/api/auth/me", headers=bearer(foreign))
    assert r.status_code == 401 and "different database" in r.json()["detail"]
    legacy = create_token(JWT_SECRET, user_id=uid, role="customer", expires_minutes=5)  # no db claim at all
    assert client.get("/api/auth/me", headers=bearer(legacy)).status_code == 401
