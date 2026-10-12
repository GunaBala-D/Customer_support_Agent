import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_container, get_session, require_admin
from app.config.settings import REPO_ROOT
from app.models.db import Event
from app.services.container import Container
from app.services.metrics import compute_metrics

router = APIRouter(prefix="/api", tags=["admin"])
EVAL_REPORT = REPO_ROOT / "evaluation" / "reports" / "latest.json"


@router.get("/health")
def health(c: Container = Depends(get_container), session: Session = Depends(get_session)):
    try:
        session.execute(select(1))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    llm = c.llm.health()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": {"ok": db_ok},
        "llm": llm,
        "embeddings": {"model": c.embedder.model},
        "vector_store": {"backend": c.settings.vector_store, "chunks": c.store.count()},
        "cache": {"backend": c.cache.backend},
    }


@router.get("/metrics", dependencies=[Depends(require_admin)])
def metrics(days: int = Query(default=30, ge=1, le=365), session: Session = Depends(get_session)):
    return compute_metrics(session, days)


@router.get("/events", dependencies=[Depends(require_admin)])
def events(name: str | None = None, limit: int = Query(default=100, ge=1, le=500), session: Session = Depends(get_session)):
    q = select(Event).order_by(Event.id.desc()).limit(limit)
    if name:
        q = q.where(Event.name == name)
    return [{"id": e.id, "request_id": e.request_id, "conversation_id": e.conversation_id, "event": e.name,
             "payload": e.payload, "created_at": e.created_at} for e in session.scalars(q)]


@router.get("/evaluation/latest", dependencies=[Depends(require_admin)])
def latest_evaluation():
    path: Path = EVAL_REPORT
    if not path.exists():
        raise HTTPException(404, "No evaluation report yet. Run: python -m evaluation.run")
    return json.loads(path.read_text(encoding="utf-8"))
