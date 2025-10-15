from __future__ import annotations

import secrets
from typing import Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.security.session import SessionManager, session_manager

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DEFAULT_EXEMPT_PREFIXES = (
    "/auth/login",
    "/auth/register",
    "/auth/password/forgot",
    "/auth/password/reset",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        manager: SessionManager | None = None,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._session_manager = manager or session_manager
        self._exempt_paths = tuple(exempt_paths or ())
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next):
        if not self._settings.enable_csrf:
            return await call_next(request)

        if request.method.upper() in SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in (*DEFAULT_EXEMPT_PREFIXES, *self._exempt_paths)):
            return await call_next(request)

        header_token = (
            request.headers.get("X-CSRF-Token")
            or request.headers.get("X-CSRFToken")
            or request.headers.get("CSRF-Token")
        )

        if not header_token:
            content_type = request.headers.get("content-type", "").lower()
            should_check_form = content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith(
                "multipart/form-data"
            )
            if should_check_form:
                try:
                    # BaseHTTPMiddleware consumes the request stream the first time it is read.
                    # Populate the cached body so downstream handlers still receive the payload.
                    await request.body()
                    form = await request.form()
                except Exception:  # pragma: no cover - fall back to header validation on parse errors
                    form = None
                if form and "_csrf" in form:
                    header_token = form.get("_csrf")

        session = await self._session_manager.load_session(request, allow_inactive=False)
        if not session:
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})

        if not header_token:
            return JSONResponse(status_code=403, content={"detail": "CSRF token missing"})

        if not secrets.compare_digest(header_token, session.csrf_token):
            return JSONResponse(status_code=403, content={"detail": "CSRF token mismatch"})

        return await call_next(request)
