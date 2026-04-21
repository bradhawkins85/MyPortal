from __future__ import annotations

import time
from typing import Iterable
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import (
    log_debug,
    log_error,
    log_info,
    reset_request_context,
    set_request_context,
)
from app.core.log_redaction import redact_headers
from app.core.logging import log_debug, log_error, log_info
from app.security.client_ip import get_client_ip


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all HTTP requests and responses."""

    def __init__(
        self,
        app,
        *,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.exempt_paths = tuple(exempt_paths or ())

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request_id = request.headers.get("x-request-id") or str(uuid4())
        request.state.request_id = request_id

        # Skip logging for exempt paths (e.g., static files) but still bind the
        # request id into the response so callers can correlate any errors.
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            tokens = set_request_context(request_id=request_id, route=path)
            try:
                response = await call_next(request)
            finally:
                reset_request_context(tokens)
            response.headers["X-Request-ID"] = request_id
            return response

        # Get client IP in a proxy-aware way that honours TRUSTED_PROXIES.
        client_ip = get_client_ip(request, default="unknown")

        # Bind contextvars so every log line emitted while processing this
        # request is automatically tagged with request_id, route and IP.
        tokens = set_request_context(
            request_id=request_id,
            route=path,
            client_ip=client_ip,
        )

        # Log incoming request
        # Log incoming request with redacted headers to avoid leaking
        # Authorization/Cookie/X-API-Key values into log aggregation systems.
        start_time = time.time()
        log_debug(
            "Incoming request",
            method=request.method,
            user_agent=request.headers.get("user-agent", "unknown"),
            headers=redact_headers(dict(request.headers)),
        )

        # Process request
        try:
            response = await call_next(request)
        except Exception as exc:  # pragma: no cover - defensive logging
            duration = time.time() - start_time
            log_error(
                "Request raised unhandled exception",
                exc=exc,
                event="request.unhandled_exception",
                method=request.method,
                duration_ms=round(duration * 1000, 2),
            )
            reset_request_context(tokens)
            raise

        # Calculate request duration
        duration = time.time() - start_time

        # Log response
        log_function = log_error if response.status_code >= 500 else log_info
        message = (
            "Request completed with server error"
            if response.status_code >= 500
            else "Request completed"
        )

        log_function(
            message,
            method=request.method,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )

        response.headers["X-Request-ID"] = request_id
        reset_request_context(tokens)
        return response
