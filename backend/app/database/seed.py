"""Deterministic, realistic seed data: 120 customers, 56 products, 240 orders, 45 tickets.

Order timelines are internally consistent (shipped after created, delivered after shipped, status
matches the dates) and a few hand-written scenarios exist for demos and tests:

  CUST-1001 Aisha Khan   ORD-1001 processing (cancellable)   ORD-1002 delivered 10 days ago (refundable)
                         ORD-1003 delivered 45 days ago (outside return window)   ORD-1024 shipped
  CUST-1002 Daniel Martinez   ORD-1004 processing  (someone else's order: permission tests)
"""
import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import Customer, Order, OrderItem, Product, SupportTicket, utcnow

FIRST = ("Aisha Daniel Priya Liam Sofia Noah Mei Omar Elena Lucas Fatima Ethan Hana Mateo Zara Arjun Chloe Kenji Isla Rafael "
         "Nadia Samuel Anika Victor Leila Owen Yuki Dmitri Amara Tobias Ingrid Carlos Layla Felix Sana Hugo Ruth Jamal Tara Ivan").split()
LAST = ("Khan Martinez Sharma Johnson Rossi Okafor Chen Haddad Petrov Silva Ali Brooks Tanaka Garcia Malik Nair Dubois Sato Campbell "
        "Costa Rahman Adeyemi Kapoor Novak Farah Murphy Ito Volkov Mensah Becker Larsen Reyes Aziz Weber Iqbal Moreau Cohen Davis Patel Kowalski").split()
CITIES = ["Austin", "Toronto", "London", "Berlin", "Singapore", "Sydney", "Chicago", "Dublin", "Mumbai", "Seattle", "Lisbon", "Denver"]
CARRIERS = ["UPS", "FedEx", "USPS", "DHL"]

PRODUCTS = [  # (category, name, price, warranty_months)
    ("Audio", "AcmeBuds Pro Wireless Earbuds", 129.00, 12), ("Audio", "AcmeBuds Lite Earbuds", 59.00, 12),
    ("Audio", "Cirrus ANC Over-Ear Headphones", 249.00, 12), ("Audio", "Cirrus Studio Wired Headphones", 89.00, 12),
    ("Audio", "Stratus Portable Bluetooth Speaker", 79.00, 12), ("Audio", "Stratus Mini Speaker", 39.00, 12),
    ("Audio", "Stratus Soundbar 2.1", 199.00, 12), ("Audio", "Podcast USB Microphone", 99.00, 12),
    ("Smart Home", "Halo Smart Bulb 4-Pack", 34.00, 12), ("Smart Home", "Halo Smart Plug 2-Pack", 24.00, 12),
    ("Smart Home", "Halo Video Doorbell", 149.00, 24), ("Smart Home", "Halo Indoor Camera", 49.00, 12),
    ("Smart Home", "Halo Smart Thermostat", 169.00, 24), ("Smart Home", "Halo Hub", 69.00, 12),
    ("Smart Home", "Halo Motion Sensor 3-Pack", 39.00, 12), ("Smart Home", "Halo Smart Lock", 189.00, 24),
    ("Computing", "Orbit Mechanical Keyboard", 119.00, 12), ("Computing", "Orbit Wireless Mouse", 39.00, 12),
    ("Computing", "Orbit Ergonomic Mouse", 69.00, 12), ("Computing", "Orbit 27in 4K Monitor", 389.00, 24),
    ("Computing", "Orbit USB-C Dock 11-in-1", 129.00, 12), ("Computing", "Orbit Laptop Stand", 45.00, 12),
    ("Computing", "Orbit 1TB Portable SSD", 109.00, 36), ("Computing", "Orbit 2TB Portable SSD", 189.00, 36),
    ("Computing", "Orbit Webcam 1080p", 69.00, 12), ("Computing", "Orbit Laptop Sleeve 14in", 29.00, 6),
    ("Mobile", "Zephyr Phone Case", 25.00, 6), ("Mobile", "Zephyr Screen Protector 2-Pack", 15.00, 6),
    ("Mobile", "Zephyr Wireless Charging Pad", 35.00, 12), ("Mobile", "Zephyr MagSafe Car Mount", 29.00, 12),
    ("Mobile", "Zephyr 65W USB-C Charger", 39.00, 12), ("Mobile", "Zephyr Braided USB-C Cable 2m", 14.00, 12),
    ("Mobile", "Zephyr Tablet Stand", 32.00, 12), ("Mobile", "Zephyr Phone Gimbal", 99.00, 12),
    ("Wearables", "Pulse Fitness Band", 59.00, 12), ("Wearables", "Pulse Smartwatch S2", 199.00, 12),
    ("Wearables", "Pulse Smartwatch Pro", 329.00, 24), ("Wearables", "Pulse Sleep Ring", 249.00, 12),
    ("Wearables", "Pulse Watch Strap 3-Pack", 29.00, 6), ("Wearables", "Pulse Kids Tracker", 79.00, 12),
    ("Gaming", "Vortex Gaming Controller", 59.00, 12), ("Gaming", "Vortex Pro Controller", 79.00, 12),
    ("Gaming", "Vortex Gaming Headset", 89.00, 12), ("Gaming", "Vortex RGB Mouse Pad XL", 29.00, 6),
    ("Gaming", "Vortex Capture Card", 139.00, 12), ("Gaming", "Vortex Controller Charging Dock", 25.00, 12),
    ("Power", "Ember 20,000mAh Power Bank", 49.00, 12), ("Power", "Ember 10,000mAh Slim Power Bank", 29.00, 12),
    ("Power", "Ember 300W Portable Power Station", 279.00, 24), ("Power", "Ember Surge Protector 6-Outlet", 27.00, 24),
    ("Power", "Ember Solar Charger 21W", 59.00, 12), ("Power", "Ember Travel Adapter", 22.00, 12),
    ("Cameras", "Lumen Action Camera 4K", 179.00, 12), ("Cameras", "Lumen Mini Drone", 299.00, 12),
    ("Cameras", "Lumen Tripod Pro", 49.00, 12), ("Cameras", "Lumen 128GB microSD", 19.00, 36),
]

TICKET_TEMPLATES = [
    ("order", "Package arrived damaged", "The box was crushed and the product has a cracked casing. Photos attached.", "normal"),
    ("order", "Wrong item received", "I ordered a different model than the one delivered.", "normal"),
    ("order", "Order delayed beyond estimate", "Tracking has not updated for several days.", "normal"),
    ("payment", "Charged twice for one order", "My card statement shows two identical charges.", "high"),
    ("payment", "Payment taken but no confirmation email", "I was charged but never received an order confirmation.", "high"),
    ("refund", "Refund not received yet", "The return was delivered to your warehouse but no refund has appeared.", "normal"),
    ("refund", "Question about return label", "I did not receive a prepaid label for my return.", "low"),
    ("technical", "Device will not pair", "The earbuds do not show up in the Bluetooth list.", "normal"),
    ("technical", "Battery drains quickly", "Battery lasts only a few hours after the latest firmware update.", "normal"),
    ("technical", "Defective on arrival", "The unit does not power on out of the box.", "high"),
    ("general", "Update delivery address", "I moved and want to change the address on a pending order.", "normal"),
    ("general", "Warranty claim", "The product stopped working after 8 months; I'd like to file a warranty claim.", "normal"),
    ("complaint", "Poor support experience", "I was waiting on hold for a long time and nobody resolved my issue.", "high"),
]


def seed_database(session: Session, seed: int = 42, force: bool = False) -> dict:
    if not force and session.scalar(select(func.count()).select_from(Customer)):
        return {"seeded": False}
    rng = random.Random(seed)
    now = utcnow()

    products = []
    for i, (cat, name, price, warranty) in enumerate(PRODUCTS, 1):
        products.append(Product(sku=f"NG-{cat[:3].upper()}-{i:03d}", name=name, category=cat, price=price,
                                warranty_months=warranty, description=f"{name} from the Acme Electronics {cat} range."))
    session.add_all(products)

    customers = []
    used = set()
    for i in range(120):
        while True:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            if (first, last) not in used:
                used.add((first, last))
                break
        cid = f"CUST-{1001 + i}"
        customers.append(Customer(
            id=cid, name=f"{first} {last}", email=f"{first}.{last}{i}@example.com".lower(),
            phone=f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
            tier=rng.choices(["standard", "plus", "premium"], [70, 22, 8])[0], city=rng.choice(CITIES),
            created_at=now - timedelta(days=rng.randint(30, 900))))
    customers[0].name, customers[0].email, customers[0].tier = "Aisha Khan", "aisha.khan@example.com", "plus"
    customers[1].name, customers[1].email = "Daniel Martinez", "daniel.martinez@example.com"
    session.add_all(customers)
    session.flush()

    def make_order(oid, cust_id, age_days, status=None, deliver_after=None):
        created = now - timedelta(days=age_days)
        picked = rng.sample(products, rng.choice([1, 1, 2, 2, 3, 4]))
        items = [OrderItem(product_sku=p.sku, quantity=rng.choice([1, 1, 1, 2]), unit_price=p.price) for p in picked]
        total = round(sum(i.quantity * i.unit_price for i in items), 2)
        shipped = delivered = carrier = tracking = None
        if status is None:  # derive a consistent state from the age
            r = rng.random()
            if r < 0.07:
                status = "cancelled"
            elif age_days <= 1:
                status = rng.choice(["pending", "processing"])
            else:
                s = created + timedelta(days=1)
                dlv = s + timedelta(days=rng.randint(2, 5))
                status = "delivered" if dlv <= now else "shipped" if s <= now else "processing"
                if status == "delivered" and rng.random() < 0.04:
                    status = "refunded"
        if status in {"shipped", "delivered", "refunded"}:
            shipped = created + timedelta(days=1)
            carrier = rng.choice(CARRIERS)
            tracking = f"{carrier[:2].upper()}{rng.randint(10**9, 10**10 - 1)}"
        if status in {"delivered", "refunded"}:
            delivered = now - timedelta(days=deliver_after) if deliver_after is not None else shipped + timedelta(days=rng.randint(2, 5))
        pay = {"cancelled": "refunded", "refunded": "refunded"}.get(status, "paid")
        o = Order(id=oid, customer_id=cust_id, status=status, payment_status=pay, total=total, created_at=created,
                  shipped_at=shipped, delivered_at=delivered, carrier=carrier, tracking_number=tracking, items=items)
        session.add(o)

    make_order("ORD-1001", "CUST-1001", 0.5, "processing")
    make_order("ORD-1002", "CUST-1001", 16, "delivered", deliver_after=10)
    make_order("ORD-1003", "CUST-1001", 52, "delivered", deliver_after=45)
    make_order("ORD-1004", "CUST-1002", 0.4, "processing")
    make_order("ORD-1024", "CUST-1001", 4, "shipped")
    for n in range(1005, 1241):
        if n == 1024:
            continue
        make_order(f"ORD-{n}", rng.choice(customers[2:]).id, rng.choice([0, 1, 2, 3, 5, 8, 12, 20, 30, 45, 60, 90, 120]) + rng.random())
    session.flush()

    for _ in range(45):
        cat, subject, desc, prio = rng.choice(TICKET_TEMPLATES)
        cust = rng.choice(customers)
        session.add(SupportTicket(
            customer_id=cust.id, subject=subject,
            description=desc, category=cat, priority=prio, status=rng.choice(["open", "in_progress", "resolved", "resolved"]),
            created_at=now - timedelta(days=rng.randint(0, 60))))
    session.commit()
    return {"seeded": True, **{n: session.scalar(select(func.count()).select_from(m)) for n, m in
            (("customers", Customer), ("products", Product), ("orders", Order), ("tickets", SupportTicket))}}
