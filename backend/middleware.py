"""Request-id tagging, access logging, security headers, and the optional
API-key auth dependency -- kept separate from server.py so the endpoint
definitions stay readable."""

import logging
import time
import uuid
from typing import Optional

from fastapi import Header
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from .config import settings
from .errors import AuthError
from .logging_config import request_id_var

access_logger = logging.getLogger("prq.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates X-Request-ID and logs one line per request with
    method, path, status, and duration."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(req_id)
        request.state.request_id = req_id
        start = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = (time.monotonic() - start) * 1000
        response.headers["X-Request-ID"] = req_id
        access_logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency guarding every /prq/* route except the health
    probes. No-op when BACKEND_API_KEY isn't set (local/dev default)."""
    if not settings.auth_enabled:
        return
    if not x_api_key or x_api_key != settings.backend_api_key:
        raise AuthError("Missing or invalid X-API-Key header.")
