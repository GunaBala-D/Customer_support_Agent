"""Tool registry + executor.

Every tool call, from the agent or from a REST endpoint, goes through `run_tool`, which enforces:
  1. argument validation (strict patterns; the LLM never supplies free-form identifiers)
  2. ownership / permission checks (a customer can only touch their own data)
  3. a confirmation gate for destructive tools (enforced here, not just in the agent)
  4. an audit row in `tool_calls` and a structured event with the duration
"""
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.db import ToolCall

ORDER_ID_PATTERN = re.compile(r"^ORD-\d{3,8}$")
CUSTOMER_ID_PATTERN = re.compile(r"^CUST-\d{4,6}$")


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ToolContext:
    session: Session
    customer_id: str | None = None
    conversation_id: str | None = None
    is_admin: bool = False
    events: Any = None  # EventLogger | None


@dataclass
class ToolResult:
    tool: str
    success: bool
    data: dict | None = None
    error: str | None = None
    error_code: str | None = None
    duration_ms: float = 0.0
    arguments: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "success": self.success,
            "error": self.error,
            "error_code": self.error_code,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., dict]
    description: str
    destructive: bool = False


TOOLS: dict[str, ToolSpec] = {}


def register(name: str, description: str, destructive: bool = False):
    def deco(fn):
        TOOLS[name] = ToolSpec(name, fn, description, destructive)
        return fn

    return deco


def run_tool(ctx: ToolContext, name: str, args: dict, confirmed: bool = False) -> ToolResult:
    start = time.perf_counter()
    result = ToolResult(name, False, arguments=dict(args))
    try:
        spec = TOOLS.get(name)
        if spec is None:
            raise ToolError("unknown_tool", f"Unknown tool '{name}'")
        if spec.destructive and not confirmed:
            raise ToolError("confirmation_required", f"'{name}' changes data and needs explicit user confirmation")
        result.data = spec.fn(ctx, **args)
        result.success = True
    except ToolError as exc:
        ctx.session.rollback()
        result.error, result.error_code = exc.message, exc.code
    except TypeError as exc:  # wrong/missing arguments
        ctx.session.rollback()
        result.error, result.error_code = f"Invalid arguments: {exc}", "invalid_arguments"
    except Exception as exc:  # noqa: BLE001 - last line of defence, surfaced as a failed tool call
        ctx.session.rollback()
        result.error, result.error_code = f"Internal tool error: {type(exc).__name__}", "internal_error"
    result.duration_ms = (time.perf_counter() - start) * 1000

    ctx.session.add(
        ToolCall(
            conversation_id=ctx.conversation_id,
            tool_name=name,
            arguments=result.arguments,
            result=result.data if result.success else None,
            success=result.success,
            error=(result.error or "")[:300] or None,
            confirmed=confirmed,
            duration_ms=result.duration_ms,
        )
    )
    ctx.session.commit()
    if ctx.events:
        ctx.events.emit(
            "tool_call", ctx.conversation_id, tool=name, duration_ms=round(result.duration_ms, 1),
            success=result.success, error_code=result.error_code,
        )
    return result
