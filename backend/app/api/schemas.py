from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )


class Citation(BaseModel):
    index: int
    document_id: str
    chunk_id: str
    filename: str
    page: int | None = None
    section: str | None = None
    label: str
    score: float
    snippet: str


class ToolCallInfo(BaseModel):
    tool: str
    arguments: dict
    success: bool
    error: str | None = None
    error_code: str | None = None
    duration_ms: float


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: int
    answer: str
    intent: str | None
    status: Literal["answered", "fallback", "needs_confirmation", "completed", "escalated", "clarification", "error"]
    escalated: bool
    ticket: str | None = None
    citations: list[Citation]
    tool_calls: list[ToolCallInfo]
    trace: list[dict]
    validation: dict | None = None
    metrics: dict


class CancelRequest(BaseModel):
    confirm: bool = False


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=4000)
    category: Literal["general", "order", "payment", "refund", "complaint", "technical"] = "general"
    priority: Literal["low", "normal", "high"] = "normal"
    order_id: str | None = Field(default=None, pattern=r"^ORD-\d{3,8}$")
