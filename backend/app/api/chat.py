from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Actor, get_actor, get_container, get_optional_customer, get_session
from app.api.schemas import ChatRequest, ChatResponse
from app.models.db import Conversation, Message
from app.services.container import Container
from app.services.orchestrator import ConversationAccessError, handle_chat

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    customer_id: str | None = Depends(get_optional_customer),
    c: Container = Depends(get_container),
    session: Session = Depends(get_session),
):
    """Send one customer message. Pass `X-Customer-Id` to enable order/account tools."""
    try:
        return handle_chat(c, session, message=body.message.strip(), conversation_id=body.conversation_id, customer_id=customer_id)
    except ConversationAccessError as exc:
        raise HTTPException(403, str(exc)) from exc


def _own(conv: Conversation, actor: Actor) -> bool:
    return actor.is_admin or (actor.customer_id is not None and conv.customer_id == actor.customer_id)


@router.get("/conversations")
def list_conversations(
    status: str | None = Query(default=None, pattern="^(open|resolved|escalated)$"),
    limit: int = Query(default=50, ge=1, le=200),
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
):
    q = select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
    if status:
        q = q.where(Conversation.status == status)
    if not actor.is_admin:
        q = q.where(Conversation.customer_id == actor.customer_id)
    convs = session.scalars(q).all()
    counts = dict(session.execute(
        select(Message.conversation_id, func.count()).where(Message.conversation_id.in_([c.id for c in convs]))
        .group_by(Message.conversation_id)).all()) if convs else {}
    return [
        {"id": c.id, "customer_id": c.customer_id, "status": c.status, "title": c.title,
         "message_count": counts.get(c.id, 0), "created_at": c.created_at, "updated_at": c.updated_at}
        for c in convs
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    conv = session.get(Conversation, conversation_id)
    if conv is None or not _own(conv, actor):
        raise HTTPException(404, "Conversation not found.")  # same answer for "missing" and "not yours"
    return {
        "id": conv.id, "customer_id": conv.customer_id, "status": conv.status, "title": conv.title,
        "memory": {k: v for k, v in (conv.state or {}).items() if k in {"last_order_id", "pending_action", "ticket_ref"}},
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "intent": m.intent, "status": m.response_status,
             "citations": m.citations or [], "trace": m.trace or [], "tool_calls": m.tool_calls or [], "latency_ms": m.latency_ms, "model": m.model,
             "created_at": m.created_at}
            for m in conv.messages
        ],
    }
