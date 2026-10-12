"""Business tools. Each returns plain JSON-serialisable dicts and raises ToolError on failure."""
from datetime import datetime, timedelta

from app.models.db import Customer, Order, SupportTicket, utcnow
from app.tools.executor import CUSTOMER_ID_PATTERN, ORDER_ID_PATTERN, ToolContext, ToolError, register

RETURN_WINDOW_DAYS = 30  # must match data/documents/refund_policy.md
CANCELLABLE_STATUSES = {"pending", "processing"}
TICKET_CATEGORIES = {"general", "order", "payment", "refund", "complaint", "technical"}
TICKET_PRIORITIES = {"low", "normal", "high"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def _owned_order(ctx: ToolContext, order_id: str) -> Order:
    if not isinstance(order_id, str) or not ORDER_ID_PATTERN.match(order_id):
        raise ToolError("invalid_arguments", "Order IDs look like ORD-1234.")
    if not ctx.is_admin and not ctx.customer_id:
        raise ToolError("auth_required", "You need to be signed in to access order information.")
    order = ctx.session.get(Order, order_id)
    # Same message for "missing" and "someone else's": do not reveal other customers' order IDs.
    if order is None or (not ctx.is_admin and order.customer_id != ctx.customer_id):
        raise ToolError("not_found", f"I couldn't find order {order_id} on your account.")
    return order


@register("get_order_status", "Look up the status, shipping and payment state of one order.")
def get_order_status(ctx: ToolContext, order_id: str) -> dict:
    o = _owned_order(ctx, order_id)
    return {
        "order_id": o.id,
        "status": o.status,
        "payment_status": o.payment_status,
        "total": o.total,
        "currency": o.currency,
        "created_at": _iso(o.created_at),
        "shipped_at": _iso(o.shipped_at),
        "delivered_at": _iso(o.delivered_at),
        "carrier": o.carrier,
        "tracking_number": o.tracking_number,
        "cancellable": o.status in CANCELLABLE_STATUSES,
    }


@register("get_order_items", "List the products in one order.")
def get_order_items(ctx: ToolContext, order_id: str) -> dict:
    o = _owned_order(ctx, order_id)
    return {
        "order_id": o.id,
        "items": [
            {"sku": i.product_sku, "name": i.product.name, "quantity": i.quantity, "unit_price": i.unit_price}
            for i in o.items
        ],
    }


@register("get_customer_details", "Return the signed-in customer's own profile.")
def get_customer_details(ctx: ToolContext, customer_id: str) -> dict:
    if not CUSTOMER_ID_PATTERN.match(customer_id or ""):
        raise ToolError("invalid_arguments", "Customer IDs look like CUST-1001.")
    if not ctx.is_admin and customer_id != ctx.customer_id:
        raise ToolError("forbidden", "You can only view your own account details.")
    c = ctx.session.get(Customer, customer_id)
    if c is None:
        raise ToolError("not_found", "I couldn't find that customer account.")
    phone = c.phone or ""
    return {
        "customer_id": c.id,
        "name": c.name,
        "email": c.email,
        "phone_masked": f"***{phone[-4:]}" if phone else None,
        "tier": c.tier,
        "city": c.city,
        "member_since": c.created_at.date().isoformat(),
        "order_count": len(c.orders),
    }


@register("check_refund_eligibility", "Check whether an order can be returned for a refund.")
def check_refund_eligibility(ctx: ToolContext, order_id: str) -> dict:
    o = _owned_order(ctx, order_id)
    base = {"order_id": o.id, "status": o.status, "window_days": RETURN_WINDOW_DAYS}
    if o.payment_status == "refunded" or o.status == "refunded":
        return {**base, "eligible": False, "reason": "already_refunded"}
    if o.status == "cancelled":
        return {**base, "eligible": False, "reason": "cancelled"}
    if o.status != "delivered" or o.delivered_at is None:
        return {**base, "eligible": False, "reason": "not_delivered"}
    days = (utcnow() - o.delivered_at).days
    deadline = o.delivered_at + timedelta(days=RETURN_WINDOW_DAYS)
    if days > RETURN_WINDOW_DAYS:
        return {**base, "eligible": False, "reason": "window_expired", "days_since_delivery": days,
                "deadline": deadline.date().isoformat()}
    return {**base, "eligible": True, "reason": "within_window", "days_since_delivery": days,
            "deadline": deadline.date().isoformat()}


@register("create_support_ticket", "Open a ticket for a human support agent.")
def create_support_ticket(
    ctx: ToolContext,
    subject: str,
    description: str,
    category: str = "general",
    priority: str = "normal",
    order_id: str | None = None,
) -> dict:
    subject, description = (subject or "").strip(), (description or "").strip()
    if not subject or not description:
        raise ToolError("invalid_arguments", "A ticket needs a subject and a description.")
    if category not in TICKET_CATEGORIES or priority not in TICKET_PRIORITIES:
        raise ToolError("invalid_arguments", "Invalid ticket category or priority.")
    if order_id:
        _owned_order(ctx, order_id)
    ticket = SupportTicket(
        customer_id=ctx.customer_id,
        order_id=order_id,
        conversation_id=ctx.conversation_id,
        subject=subject[:200],
        description=description[:4000],
        category=category,
        priority=priority,
    )
    ctx.session.add(ticket)
    ctx.session.commit()
    return {"ticket_id": ticket.id, "reference": ticket.reference, "status": ticket.status,
            "priority": ticket.priority, "category": ticket.category}


@register("cancel_order", "Cancel an order that has not shipped yet.", destructive=True)
def cancel_order(ctx: ToolContext, order_id: str) -> dict:
    o = _owned_order(ctx, order_id)
    if o.status == "cancelled":
        raise ToolError("already_cancelled", f"Order {o.id} is already cancelled.")
    if o.status not in CANCELLABLE_STATUSES:
        raise ToolError("not_cancellable", f"Order {o.id} is {o.status} and can no longer be cancelled.")
    o.status = "cancelled"
    if o.payment_status == "paid":
        o.payment_status = "refund_pending"
    note = f"Cancelled by customer on {utcnow().date().isoformat()}"
    o.notes = f"{o.notes}; {note}" if o.notes else note
    ctx.session.commit()
    return {"order_id": o.id, "status": o.status, "payment_status": o.payment_status, "refund_due": o.total}
