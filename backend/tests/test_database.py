import re

from sqlalchemy import func, select

from app.models.db import Customer, Order, OrderItem, Product, SupportTicket


def test_seed_volume(session):
    def n(m):
        return session.scalar(select(func.count()).select_from(m))

    assert n(Customer) >= 100 and n(Order) >= 200 and n(Product) >= 50 and n(SupportTicket) >= 40


def test_ids_follow_patterns(session):
    assert all(re.fullmatch(r"CUST-\d{4}", c) for (c,) in session.execute(select(Customer.id)))
    assert all(re.fullmatch(r"ORD-\d{4}", o) for (o,) in session.execute(select(Order.id)))


def test_order_timelines_are_consistent(session):
    for o in session.scalars(select(Order)):
        if o.shipped_at:
            assert o.shipped_at >= o.created_at
        if o.delivered_at:
            assert o.shipped_at and o.delivered_at >= o.shipped_at
        if o.status in {"pending", "processing", "cancelled"}:
            assert o.delivered_at is None
        if o.status in {"shipped", "delivered"}:
            assert o.carrier and o.tracking_number


def test_order_totals_match_items(session):
    for o in session.scalars(select(Order).limit(60)):
        assert abs(o.total - sum(i.quantity * i.unit_price for i in o.items)) < 0.01
    assert session.scalar(select(func.count()).select_from(OrderItem)) > 200


def test_demo_scenarios_exist(session):
    assert session.get(Order, "ORD-1001").status == "processing"
    assert session.get(Order, "ORD-1024").status == "shipped"
    assert session.get(Order, "ORD-1004").customer_id == "CUST-1002"


def test_seed_is_idempotent(session):
    from app.database.seed import seed_database

    assert seed_database(session) == {"seeded": False}


def test_sql_injection_string_is_just_data(session):
    assert session.get(Order, "ORD-1001'; DROP TABLE orders;--") is None
    assert session.scalar(select(func.count()).select_from(Order)) >= 200
