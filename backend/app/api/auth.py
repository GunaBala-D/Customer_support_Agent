"""Login, current user, password change, and (admin) user management."""
import hashlib
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_container, get_principal, get_session, require_admin, require_roles
from app.models.db import Customer, User, utcnow
from app.services.container import Container
from app.services.security import burn_password_check, create_token, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "customer_id": u.customer_id, "is_active": u.is_active}


def _check_email(v: str) -> str:
    v = v.strip().lower()
    if not EMAIL_RE.match(v) or len(v) > 200:
        raise ValueError("enter a valid e-mail address")
    return v


class LoginBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class UserCreate(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["customer", "agent", "admin"]
    customer_id: str | None = Field(default=None, pattern=r"^CUST-\d{4,6}$")
    _email = field_validator("email")(_check_email)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Literal["customer", "agent", "admin"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


@router.post("/auth/login")
def login(body: LoginBody, c: Container = Depends(get_container), session: Session = Depends(get_session)):
    user = session.scalars(select(User).where(User.email == body.email.strip().lower())).first()
    if user is None:
        burn_password_check(body.password)  # same cost whether or not the account exists
    ok = user is not None and user.is_active and verify_password(body.password, user.password_hash)
    if not ok:
        c.events.emit("login_failed", None, email_sha=hashlib.sha256(body.email.strip().lower().encode()).hexdigest()[:10])
        raise HTTPException(401, "Invalid e-mail or password.")
    user.last_login_at = utcnow()
    session.commit()
    token = create_token(c.settings.jwt_secret, user_id=user.id, role=user.role, expires_minutes=c.settings.jwt_expire_minutes,
                         db=c.db_fingerprint)
    c.events.emit("login", None, user_id=user.id, role=user.role)
    return {"access_token": token, "token_type": "bearer", "expires_in": c.settings.jwt_expire_minutes * 60, "user": _user_out(user)}


@router.get("/auth/me")
def me(p: Principal | None = Depends(get_principal), session: Session = Depends(get_session)):
    if p is None:
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    user = session.get(User, p.user_id) if p.user_id else None
    return _user_out(user) if user else {"id": None, "email": None, "name": p.name, "role": p.role, "customer_id": p.customer_id, "is_active": True}


@router.post("/auth/change-password")
def change_password(body: PasswordChange, p: Principal = Depends(require_roles("customer", "agent", "admin")),
                    session: Session = Depends(get_session)):
    user = session.get(User, p.user_id) if p.user_id else None
    if user is None:
        raise HTTPException(400, "Only password-based accounts can change their password.")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect.")
    user.password_hash = hash_password(body.new_password)
    session.commit()
    return {"ok": True}


# ---- user management (admin) -------------------------------------------------------------------------------------

@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(session: Session = Depends(get_session)):
    return [{**_user_out(u), "last_login_at": u.last_login_at, "created_at": u.created_at}
            for u in session.scalars(select(User).order_by(User.role, User.email))]


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
def create_user(body: UserCreate, session: Session = Depends(get_session)):
    if body.role == "customer":
        if not body.customer_id or session.get(Customer, body.customer_id) is None:
            raise HTTPException(422, "A customer login must be linked to an existing customer_id.")
    elif body.customer_id:
        raise HTTPException(422, "Only customer logins have a customer_id.")
    if session.scalars(select(User).where(User.email == body.email)).first():
        raise HTTPException(409, "A user with this e-mail already exists.")
    user = User(email=body.email, name=body.name.strip(), password_hash=hash_password(body.password),
                role=body.role, customer_id=body.customer_id)
    session.add(user)
    session.commit()
    return _user_out(user)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, me_: Principal = Depends(require_admin), session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    demoting = (body.role is not None and body.role != "admin") or body.is_active is False
    if user.role == "admin" and demoting:
        active_admins = session.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True)))
        if user.id == me_.user_id or active_admins <= 1:
            raise HTTPException(409, "You can't disable or demote yourself or the last active admin.")
    if body.name is not None:
        user.name = body.name.strip()
    if body.role is not None:
        if body.role == "customer" and not user.customer_id:
            raise HTTPException(422, "Link the user to a customer before making them a customer login.")
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    session.commit()
    return _user_out(user)


@router.get("/demo/accounts")
def demo_accounts(c: Container = Depends(get_container), session: Session = Depends(get_session)):
    """Development convenience for the login screen. Always 404 when APP_ENV=production."""
    if c.settings.is_production or not c.settings.seed_demo_users:
        raise HTTPException(404, "Not found.")
    rows = session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.role, User.id).limit(12)).all()
    admin_pw = c.settings.admin_password or "admin-demo-1"
    return [{"email": u.email, "name": u.name, "role": u.role,
             "password": admin_pw if u.email == c.settings.admin_email else c.settings.demo_password} for u in rows]

