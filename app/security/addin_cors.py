"""Cross-origin access for the Outlook signature add-in's API.

Outlook runs the add-in's launch-event script outside the portal's origin
(classic Outlook on Windows uses a JavaScript-only runtime), so its call to the
signature API is cross-origin and carries an ``Authorization`` header, which
needs a preflight.  The portal-wide CORS policy only allows configured origins
with cookies, so this middleware answers for the add-in's API alone: any
origin, no credentials.  That is safe because the endpoint ignores cookies and
authenticates each call with the signer's Microsoft access token.

Add this middleware after ``CORSMiddleware`` so it runs first.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ADDIN_API_PATHS = frozenset({"/api/outlook-addin/signature"})
_ALLOW_METHODS = "GET, OPTIONS"
_ALLOW_HEADERS = "Authorization, Accept"
_MAX_AGE = "600"


class OutlookAddinCORSMiddleware:
    def __init__(self, app: ASGIApp, *, paths: frozenset[str] = ADDIN_API_PATHS) -> None:
        self.app = app
        self.paths = paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        if b"origin" not in headers:
            await self.app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS" and b"access-control-request-method" in headers:
            response = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": _ALLOW_METHODS,
                    "Access-Control-Allow-Headers": _ALLOW_HEADERS,
                    "Access-Control-Max-Age": _MAX_AGE,
                    "Vary": "Origin",
                },
            )
            await response(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name in (
                    "access-control-allow-credentials",
                    "access-control-allow-origin",
                ):
                    if name in response_headers:
                        del response_headers[name]
                response_headers["Access-Control-Allow-Origin"] = "*"
            await send(message)

        await self.app(scope, receive, send_with_cors)
