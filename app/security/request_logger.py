from __future__ import annotations

import time
from typing import Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import log_debug, log_error, log_info


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
        
        # Skip logging for exempt paths (e.g., static files)
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return await call_next(request)

        # Get client IP
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        else:
            client = request.client
            client_ip = client.host if client else "unknown"

        # Log incoming request
        start_time = time.time()
        log_debug(
            "Incoming request",
            method=request.method,
            path=path,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", "unknown"),
        )

        # Process request
        try:
            response = await call_next(request)
        except Exception as exc:  # pragma: no cover - defensive logging
            duration = time.time() - start_time
            log_error(
                "Request raised unhandled exception",
                method=request.method,
                path=path,
                duration_ms=round(duration * 1000, 2),
                client_ip=client_ip,
                error=str(exc),
            )
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
            path=path,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
            client_ip=client_ip,
        )

        return response
