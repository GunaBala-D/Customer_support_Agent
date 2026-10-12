"""First-run helper: seed the database and ingest the sample knowledge base if they are empty."""
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.seed import seed_database
from app.models.db import Customer, Document, User
from app.rag.ingestion import ingest_directory
from app.services.cache import bump_kb_version
from app.services.container import Container
from app.services.security import hash_password

log = logging.getLogger(__name__)

DEV_ADMIN_PASSWORD = "admin-demo-1"  # development only; production requires ADMIN_PASSWORD
DEMO_CUSTOMER_LOGINS = 8


def ensure_users(session: Session, settings) -> dict:
    """Idempotent. Creates the admin (and, outside production, demo agent/customer logins). Never overwrites a password."""
    created: list[str] = []

    def add(email, name, role, password, customer_id=None):
        email = email.lower()
        if session.scalars(select(User).where(User.email == email)).first() is None:
            session.add(User(email=email, name=name, role=role, customer_id=customer_id, password_hash=hash_password(password)))
            created.append(email)

    if settings.is_production:
        if settings.admin_password:
            if len(settings.admin_password) < 12:
                raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters in production")
            add(settings.admin_email, "Administrator", "admin", settings.admin_password)
    else:
        add(settings.admin_email, "Administrator", "admin", settings.admin_password or DEV_ADMIN_PASSWORD)
        if settings.seed_demo_users:
            add("agent@supportpilot.example", "Priya Agent", "agent", settings.demo_password)
            for cust in session.scalars(select(Customer).order_by(Customer.id).limit(DEMO_CUSTOMER_LOGINS)):
                add(cust.email, cust.name, "customer", settings.demo_password, cust.id)
    session.commit()
    return {"created": created}


def bootstrap(c: Container, *, seed: bool = True, documents: bool = True) -> dict:
    out: dict = {}
    with c.session_factory() as s:
        if seed:
            out["seed"] = seed_database(s)
        out["users"] = ensure_users(s, c.settings)
        if documents:
            docs_dir = Path(c.settings.documents_dir)
            if docs_dir.is_dir():
                docs = ingest_directory(s, c.store, c.embedder, docs_dir,
                                        chunk_size=c.settings.chunk_size, chunk_overlap=c.settings.chunk_overlap)
                out["documents"] = [d.filename for d in docs]
                bump_kb_version(c.cache)
            out["document_count"] = s.scalar(select(func.count()).select_from(Document))
    log.info("bootstrap: %s", out)
    return out
