import hashlib

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models.db import Base


def db_fingerprint(database_url: str) -> str:
    """Stable id of *which* database this is (password excluded). Binds tokens and vector collections to it."""
    safe = make_url(database_url).render_as_string(hide_password=True)
    return hashlib.sha256(safe.encode()).hexdigest()[:12]


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 8})


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
