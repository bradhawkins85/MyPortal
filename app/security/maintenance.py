"""Reject unsafe work and provide content-negotiated planned-maintenance responses."""

import asyncio

from loguru import logger
from starlette.datastructures import Headers
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.system_state import get_public_upgrade_state

# Maximum seconds to wait for the (synchronous) state-file check before
# failing open.  A hung filesystem or stale lock must not stall the event loop.
_STATE_CHECK_TIMEOUT = 2.0


class MaintenanceMiddleware:
    def __init__(self, app: ASGIApp, page_path: str) -> None:
        self.app = app
        self.page_path = page_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/health", "/healthz", "/readyz", "/upgrade-status"} or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return
        try:
            # Run in a thread so a slow/hung filesystem read cannot block the
            # entire event loop (which would stall ALL requests).
            state = await asyncio.wait_for(
                asyncio.to_thread(get_public_upgrade_state),
                timeout=_STATE_CHECK_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            # Fail open: an unreadable, slow, or corrupted state file must not
            # take the entire portal offline.
            logger.error(
                "Maintenance state check failed; allowing request through",
                path=path, error=str(exc),
            )
            await self.app(scope, receive, send)
            return
        if not state.get("maintenance"):
            await self.app(scope, receive, send)
            return
        logger.warning(
            "Blocking request during planned maintenance",
            path=path, phase=state.get("phase"),
            upgrade_id=state.get("upgrade_id"),
        )
        headers = {"Retry-After": "15", "Cache-Control": "no-store"}
        method = scope.get("method", "GET").upper()
        accepts_html = "text/html" in Headers(scope=scope).get("accept", "")
        if path.startswith("/api/") or method not in {"GET", "HEAD", "OPTIONS"} or not accepts_html:
            response: Response = JSONResponse(
                {"error": "upgrade_in_progress", "message": state.get("message"),
                 "upgrade_id": state.get("upgrade_id"), "phase": state.get("phase"),
                 "retry_after": 15}, status_code=503, headers=headers
            )
        else:
            response = FileResponse(self.page_path, status_code=503, media_type="text/html", headers=headers)
        await response(scope, receive, send)
