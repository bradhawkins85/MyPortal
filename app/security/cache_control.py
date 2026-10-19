from __future__ import annotations

from typing import Iterable

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings


class CacheControlMiddleware:
    """Middleware to prevent caching by proxy servers and CDNs.
    
    When enabled via DISABLE_CACHING environment variable, this middleware adds
    headers to prevent response caching. Static files are exempt from this restriction.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        self.app = app
        self._exempt_paths = tuple(exempt_paths or ())
        self._settings = get_settings()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Skip if caching control is not enabled
        if scope["type"] != "http" or not self._settings.disable_caching:
            await self.app(scope, receive, send)
            return

        # Check if path is exempt (like static files)
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt_paths):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Add cache control headers to prevent caching
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
                headers["Pragma"] = "no-cache"
                headers["Expires"] = "0"
            await send(message)

        await self.app(scope, receive, send_with_headers)
