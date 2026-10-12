"""Request ID + latency logging + in-memory sliding-window rate limiter."""
import logging
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.observability import request_id_var

log = logging.getLogger("http")

RATE_LIMITED_POST_PREFIXES = ("/api/chat", "/api/documents/upload", "/api/support/tickets", "/api/orders", "/api/auth", "/api/users", "/api/settings")
LOGIN_PATH = "/api/auth/login"
LOGIN_LIMIT_PER_MINUTE = 10  # brute-force guard, independent of the general limit


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id", "")
        rid = rid if len(rid) <= 64 and rid.replace("-", "").isalnum() else str(uuid.uuid4())
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        log.info("request_id=%s %s %s -> %s %.0fms", rid, request.method, request.url.path, response.status_code, ms)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding window on state-changing endpoints. Single-process only; behind several
    workers use Redis (the same interface) or an API gateway."""

    def __init__(self, app, limit_per_minute: int):
        super().__init__(app)
        self.limit = limit_per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        if request.method == "POST" and request.url.path.startswith(RATE_LIMITED_POST_PREFIXES) and self.limit > 0:
            # Keyed by IP only: headers such as X-Customer-Id are client-controlled and could be rotated.
            client = request.client.host if request.client else "unknown"
            limit = min(self.limit, LOGIN_LIMIT_PER_MINUTE) if request.url.path == LOGIN_PATH else self.limit
            if request.url.path == LOGIN_PATH:
                client += "|login"
            now = time.monotonic()
            q = self.hits[client]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(60 - (now - q[0])))
                return JSONResponse({"detail": "Rate limit exceeded. Please slow down."}, status_code=429,
                                    headers={"Retry-After": str(retry)})
            q.append(now)
        return await call_next(request)
