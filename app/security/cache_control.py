from __future__ import annotations

from typing import Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Middleware to prevent caching by proxy servers and CDNs.
    
    When enabled via DISABLE_CACHING environment variable, this middleware adds
    headers to prevent response caching. Static files are exempt from this restriction.
    """

    def __init__(
        self,
        app,
        *,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._exempt_paths = tuple(exempt_paths or ())
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next):
        # Skip if caching control is not enabled
        if not self._settings.disable_caching:
            return await call_next(request)

        # Check if path is exempt (like static files)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._exempt_paths):
            return await call_next(request)

        # Get the response
        response = await call_next(request)

        # Add cache control headers to prevent caching
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        return response
