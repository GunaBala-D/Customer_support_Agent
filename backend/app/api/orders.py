from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import Actor, get_actor, get_container, get_session
from app.api.schemas import CancelRequest, TicketCreate
from app.models.db import Product
from app.services.container import Container
from app.tools import ToolContext, run_tool

router = APIRouter(prefix="/api", tags=["orders & support"])

_STATUS_FOR = {"not_found": 404, "forbidden": 403, "auth_required": 401, "invalid_arguments": 422,
               "confirmation_required": 409, "not_cancellable": 409, "already_cancelled": 409}


def _ctx(actor: Actor, session: Session, c: Container) -> ToolContext:
    return ToolContext(session, actor.customer_id, None, actor.is_admin, c.events)


def _raise(res):
    raise HTTPException(_STATUS_FOR.get(res.error_code, 500), res.error)


@router.get("/orders/{order_id}")
def get_order(order_id: str, actor: Actor = Depends(get_actor), c: Container = Depends(get_container),
              session: Session = Depends(get_session)):
    ctx = _ctx(actor, session, c)
    status = run_tool(ctx, "get_order_status", {"order_id": order_id.upper()})
    if not status.success:
        _raise(status)
    items = run_tool(ctx, "get_order_items", {"order_id": order_id.upper()})
    return {**status.data, "items": items.data["items"] if items.success else []}


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, body: CancelRequest, actor: Actor = Depends(get_actor),
                 c: Container = Depends(get_container), session: Session = Depends(get_session)):
    """Destructive: requires {"confirm": true}. Same confirmation gate the chat agent uses."""
    res = run_tool(_ctx(actor, session, c), "cancel_order", {"order_id": order_id.upper()}, confirmed=body.confirm)
    if not res.success:
        _raise(res)
    return res.data


@router.post("/support/tickets", status_code=201)
def create_ticket(body: TicketCreate, actor: Actor = Depends(get_actor), c: Container = Depends(get_container),
                  session: Session = Depends(get_session)):
    res = run_tool(_ctx(actor, session, c), "create_support_ticket", body.model_dump(exclude_none=True))
    if not res.success:
        _raise(res)
    return res.data


@router.get("/products/{sku}")
def get_product(sku: str, c: Container = Depends(get_container), session: Session = Depends(get_session)):
    """Public catalogue data: safe to cache."""
    key = f"product:{sku}"
    cached = c.cache.get(key)
    if cached:
        return {**cached, "cached": True}
    p = session.get(Product, sku)
    if p is None:
        raise HTTPException(404, "Product not found.")
    data = {"sku": p.sku, "name": p.name, "category": p.category, "price": p.price, "warranty_months": p.warranty_months,
            "description": p.description}
    c.cache.set(key, data, c.settings.cache_ttl_seconds)
    return {**data, "cached": False}
