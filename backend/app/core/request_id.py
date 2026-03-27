"""
request_id.py — Request correlation ID middleware.

Assigns a unique ID to every incoming HTTP request and:
  1. Sets it in thread-local context (available in all log lines)
  2. Returns it in the X-Request-ID response header
  3. Accepts an incoming X-Request-ID header (for tracing across services)

The request_id is a short 12-char hex string — compact enough for log
readability while unique enough for practical correlation (~281 trillion
values before birthday collision probability reaches 1%).
"""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging_config import set_request_context, clear_request_context

_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Accept incoming request ID or generate one
        request_id = request.headers.get(_HEADER) or secrets.token_hex(6)

        # Extract shop_domain from session cookie context if available
        # (lightweight — doesn't verify the JWT, just peeks at the claim)
        shop = _peek_shop_from_cookie(request)

        set_request_context(request_id=request_id, shop_domain=shop)
        try:
            response = await call_next(request)
            response.headers[_HEADER] = request_id
            return response
        finally:
            clear_request_context()


def _peek_shop_from_cookie(request: Request) -> str | None:
    """Extract shop_domain from session JWT without full verification.
    Used only for log enrichment — NOT for auth decisions."""
    from app.core.merchant_session import SESSION_COOKIE_NAME
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        import jwt
        # Decode without verification — this is for log context only
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("shop")
    except Exception:
        return None
