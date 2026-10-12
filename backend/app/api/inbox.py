"""Human-agent inbox: work the tickets that the assistant escalated. Admin (agent) key required."""
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_container, get_session, require_staff
from app.models.db import Conversation, Customer, Message, Order, SupportTicket, TicketNote
from app.services.container import Container

router = APIRouter(prefix="/api/support/tickets", tags=["agent inbox"], dependencies=[Depends(require_staff)])

REF_RE = re.compile(r"^TCK-(\d{1,9})$")
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


class TicketUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved"] | None = None
    priority: Literal["low", "normal", "high"] | None = None
    assignee: str | None = Field(default=None, max_length=60)
    author: str = Field(default="agent", min_length=1, max_length=60)


class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="agent", min_length=1, max_length=60)


def _author(principal: Principal, claimed: str) -> str:
    """A logged-in agent's name comes from their account, not from the request body (no impersonation)."""
    return principal.name if principal.via == "jwt" and principal.name else claimed


def _ticket(session: Session, ref: str) -> SupportTicket:
    m = REF_RE.match(ref)
    t = session.get(SupportTicket, int(m.group(1))) if m else None
    if t is None:
        raise HTTPException(404, "Ticket not found.")
    return t


def _assignee(session: Session, ticket_id: int) -> str | None:
    row = session.scalars(select(TicketNote).where(TicketNote.ticket_id == ticket_id, TicketNote.kind == "assign")
                          .order_by(TicketNote.id.desc()).limit(1)).first()
    return (row.body or None) if row else None


def _summary(session: Session, t: SupportTicket) -> dict:
    cust = session.get(Customer, t.customer_id) if t.customer_id else None
    return {"reference": t.reference, "subject": t.subject, "category": t.category, "priority": t.priority, "status": t.status,
            "customer_id": t.customer_id, "customer_name": cust.name if cust else None, "order_id": t.order_id,
            "conversation_id": t.conversation_id, "assignee": _assignee(session, t.id), "created_at": t.created_at}


@router.get("")
def list_tickets(
    status: Literal["open", "in_progress", "resolved"] | None = None,
    priority: Literal["low", "normal", "high"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=300),
    session: Session = Depends(get_session),
):
    counts = dict(session.execute(select(SupportTicket.status, func.count()).group_by(SupportTicket.status)).all())
    query = select(SupportTicket)
    if status:
        query = query.where(SupportTicket.status == status)
    if priority:
        query = query.where(SupportTicket.priority == priority)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(SupportTicket.subject.ilike(like), SupportTicket.customer_id.ilike(like),
                                SupportTicket.order_id.ilike(like), SupportTicket.description.ilike(like)))
    rows = session.scalars(query.order_by(SupportTicket.id.desc()).limit(limit)).all()
    # Work queue order: unclaimed (open) first, then in progress, resolved last; within a group high
    # priority first, then the longest-waiting ticket.
    group = {"open": 0, "in_progress": 1, "resolved": 2}
    rows.sort(key=lambda t: (group[t.status], PRIORITY_RANK[t.priority], t.created_at))
    return {"counts": {s: counts.get(s, 0) for s in ("open", "in_progress", "resolved")},
            "items": [_summary(session, t) for t in rows]}


@router.get("/{ref}")
def ticket_detail(ref: str, session: Session = Depends(get_session)):
    t = _ticket(session, ref)
    cust = session.get(Customer, t.customer_id) if t.customer_id else None
    order = session.get(Order, t.order_id) if t.order_id else None
    transcript = []
    if t.conversation_id:
        transcript = [{"role": m.role, "content": m.content, "status": m.response_status, "intent": m.intent,
                       "created_at": m.created_at}
                      for m in session.scalars(select(Message).where(Message.conversation_id == t.conversation_id).order_by(Message.id))]
    notes = session.scalars(select(TicketNote).where(TicketNote.ticket_id == t.id).order_by(TicketNote.id)).all()
    return {
        **_summary(session, t), "description": t.description,
        "customer": {"id": cust.id, "name": cust.name, "email": cust.email, "tier": cust.tier} if cust else None,
        "order": {"id": order.id, "status": order.status, "total": order.total, "payment_status": order.payment_status} if order else None,
        "transcript": transcript,
        "activity": [{"id": n.id, "author": n.author, "kind": n.kind, "body": n.body, "created_at": n.created_at} for n in notes],
    }


@router.patch("/{ref}")
def update_ticket(ref: str, body: TicketUpdate, p: Principal = Depends(require_staff), session: Session = Depends(get_session)):
    t = _ticket(session, ref)
    body.author = _author(p, body.author)
    if body.status and body.status != t.status:
        session.add(TicketNote(ticket_id=t.id, author=body.author, kind="status", body=f"{t.status} → {body.status}"))
        t.status = body.status
    if body.priority and body.priority != t.priority:
        session.add(TicketNote(ticket_id=t.id, author=body.author, kind="status", body=f"priority {t.priority} → {body.priority}"))
        t.priority = body.priority
    if body.assignee is not None and body.assignee.strip() != (_assignee(session, t.id) or ""):
        session.add(TicketNote(ticket_id=t.id, author=body.author, kind="assign", body=body.assignee.strip()))
        if t.status == "open" and body.assignee.strip():
            session.add(TicketNote(ticket_id=t.id, author=body.author, kind="status", body="open → in_progress"))
            t.status = "in_progress"
    session.commit()
    return _summary(session, t)


@router.post("/{ref}/notes", status_code=201)
def add_note(ref: str, body: NoteBody, p: Principal = Depends(require_staff), session: Session = Depends(get_session)):
    """Internal note: never shown to the customer."""
    t = _ticket(session, ref)
    body.author = _author(p, body.author)
    session.add(TicketNote(ticket_id=t.id, author=body.author, kind="note", body=body.body.strip()))
    session.commit()
    return {"ok": True}


@router.post("/{ref}/reply", status_code=201)
def reply_to_customer(ref: str, body: NoteBody, p: Principal = Depends(require_staff), c: Container = Depends(get_container),
                      session: Session = Depends(get_session)):
    """Customer-visible reply: appended to the customer's conversation as a message from a human agent."""
    t = _ticket(session, ref)
    body.author = _author(p, body.author)
    conv = session.get(Conversation, t.conversation_id) if t.conversation_id else None
    if conv is None:
        raise HTTPException(409, "This ticket has no linked conversation to reply in.")
    session.add(Message(conversation_id=conv.id, role="agent", content=body.body.strip(), response_status="answered", intent="human_reply"))
    session.add(TicketNote(ticket_id=t.id, author=body.author, kind="reply", body=body.body.strip()))
    if t.status == "open":
        session.add(TicketNote(ticket_id=t.id, author=body.author, kind="status", body="open → in_progress"))
        t.status = "in_progress"
    conv.status = "escalated"
    session.commit()
    c.events.emit("agent_reply", conv.id, ticket=t.reference, author=body.author)
    return {"ok": True}
