"""Reject unsafe work and provide content-negotiated planned-maintenance responses."""

from starlette.datastructures import Headers
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.system_state import get_public_upgrade_state


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
        state = get_public_upgrade_state()
        if not state.get("maintenance"):
            await self.app(scope, receive, send)
            return
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
