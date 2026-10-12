"""SQLAlchemy models. Works on PostgreSQL and SQLite."""
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # CUST-1001
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(20), default="standard")  # standard | plus | premium
    city: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(60), index=True)
    price: Mapped[float] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    warranty_months: Mapped[int] = mapped_column(Integer, default=12)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # ORD-1001
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    # pending | processing | shipped | delivered | cancelled | refunded
    status: Mapped[str] = mapped_column(String(20), index=True)
    # paid | refund_pending | refunded
    payment_status: Mapped[str] = mapped_column(String(20), default="paid")
    total: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    carrier: Mapped[str | None] = mapped_column(String(30))
    tracking_number: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float)
    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="general")
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # low|normal|high
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def reference(self) -> str:
        return f"TCK-{self.id:05d}"


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|resolved|escalated
    title: Mapped[str | None] = mapped_column(String(200))
    # short-term memory: last order, pending confirmation, counters (see agents/memory.py)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.id", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(30), index=True)
    # answered | fallback | needs_confirmation | completed | escalated | clarification | error
    response_status: Mapped[str | None] = mapped_column(String(20), index=True)
    citations: Mapped[list | None] = mapped_column(JSON)
    trace: Mapped[list | None] = mapped_column(JSON)  # high-level steps, NOT chain-of-thought
    tool_calls: Mapped[list | None] = mapped_column(JSON)  # summaries of tools run for this reply
    model: Mapped[str | None] = mapped_column(String(80))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    llm_ms: Mapped[float | None] = mapped_column(Float)
    retrieval_ms: Mapped[float | None] = mapped_column(Float)
    tool_ms: Mapped[float | None] = mapped_column(Float)
    retrieved_count: Mapped[int | None] = mapped_column(Integer)
    top_score: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(60), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(String(300))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(30), default="upload")  # upload | bootstrap
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    num_pages: Mapped[int | None] = mapped_column(Integer)
    num_chunks: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(100))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # also the vector-store id
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    document: Mapped[Document] = relationship(back_populates="chunks")


class TicketNote(Base):
    """Agent activity on a ticket. Kept in its own table so existing databases need no ALTER."""

    __tablename__ = "ticket_notes"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    author: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(12))  # note (internal) | reply (customer-visible) | status | assign
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    """A login. Roles: customer (bound to one customers row), agent (works tickets), admin (everything)."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(10), index=True)  # customer | agent | admin
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class Event(Base):
    """Structured observability events (request, llm_call, retrieval, tool_call, ...)."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("ix_messages_created", Message.created_at)
