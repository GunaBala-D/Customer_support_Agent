"""Request authentication and authorisation.

Credentials accepted, in order:
  1. `Authorization: Bearer <JWT>`  - the real login (roles: customer, agent, admin). The user row is re-read on every
                                      request, so deactivating a user or changing a role takes effect immediately.
  2. `X-API-Key`                    - a static service credential acting as admin (scripts/CI). Disable with ALLOW_API_KEY=false.
  3. `X-Customer-Id`                - demo identity for tests and quick demos. Never honoured when APP_ENV=production.
"""
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.models.db import Customer, User
from app.services.container import Container
from app.services.security import TokenError, decode_token

CUSTOMER_HEADER_RE = re.compile(r"^CUST-\d{4,6}$")
STAFF_ROLES = ("agent", "admin")


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_session(c: Container = Depends(get_container)) -> Iterator[Session]:
    with c.session_factory() as session:
        yield session


@dataclass
class Principal:
    role: str  # customer | agent | admin
    via: str  # jwt | api_key | demo_header
    user_id: int | None = None
    name: str = ""
    customer_id: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


@dataclass
class Actor:
    """What the order/ticket tools need to know: staff may act on any order, customers only on their own."""

    is_admin: bool
    customer_id: str | None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def get_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_customer_id: str | None = Header(default=None),
    c: Container = Depends(get_container),
    session: Session = Depends(get_session),
) -> Principal | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized("Use 'Authorization: Bearer <token>'.")
        try:
            claims = decode_token(c.settings.jwt_secret, token)
            user = session.get(User, int(claims["sub"]))
        except (TokenError, ValueError):
            raise _unauthorized("Invalid or expired token.") from None
        if claims.get("db") != c.db_fingerprint:  # issued under another database: ids may belong to different people
            raise _unauthorized("Your session belongs to a different database. Please sign in again.")
        if user is None or not user.is_active:
            raise _unauthorized("Account not found or disabled.")
        return Principal(user.role, "jwt", user.id, user.name, user.customer_id)

    configured = c.settings.admin_api_key
    if x_api_key:
        if c.settings.allow_api_key and configured and secrets.compare_digest(x_api_key.encode(), configured.encode()):
            return Principal("admin", "api_key", name="service key")
        raise _unauthorized("Invalid API key.")

    if x_customer_id and c.settings.demo_identity_enabled:
        if not CUSTOMER_HEADER_RE.match(x_customer_id) or session.get(Customer, x_customer_id) is None:
            raise _unauthorized("Unknown customer.")
        return Principal("customer", "demo_header", customer_id=x_customer_id)
    return None


def require_roles(*roles: str):
    def dependency(principal: Principal | None = Depends(get_principal)) -> Principal:
        if principal is None:
            raise _unauthorized("Authentication required.")
        if principal.role not in roles:
            raise HTTPException(403, "Your role is not allowed to do this.")
        return principal

    return dependency


require_admin = require_roles("admin")
require_staff = require_roles(*STAFF_ROLES)


def get_actor(principal: Principal | None = Depends(get_principal)) -> Actor:
    if principal is None:
        raise _unauthorized("Authentication required.")
    return Actor(principal.is_staff, principal.customer_id)


def get_optional_customer(principal: Principal | None = Depends(get_principal)) -> str | None:
    """Customer id for the chat endpoint. Staff and anonymous visitors chat as guests (no account tools)."""
    return principal.customer_id if principal and principal.role == "customer" else None
