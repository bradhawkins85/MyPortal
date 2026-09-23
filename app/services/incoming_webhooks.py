"""Central monitoring for provider-originated HTTP callbacks.

Request and response bodies are capped by ``MONITOR_BODY_LIMIT`` (4,000
characters) and passed through the shared credential/PII redactor.  The route
registry is intentionally explicit: it is both documentation and a reviewable
safeguard against accidentally monitoring normal portal or Tray-agent traffic.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
import json
import re
from typing import Any, Awaitable, Callable

from app.core.logging import log_warning
from app.services.monitored_http import sanitise_body, sanitise_headers, sanitise_url


@dataclass(frozen=True)
class IncomingRoute:
    pattern: re.Pattern[str]
    provider: str


def _route(pattern: str, provider: str) -> IncomingRoute:
    return IncomingRoute(re.compile(pattern), provider)


# Keep exact and anchored. Broad ``/api`` or ``webhook`` matching risks logging
# MyPortal/Tray traffic and makes additions impossible to audit.
INCOMING_ROUTES: tuple[IncomingRoute, ...] = (
    _route(r"^/phonewebhook/[^/]+/?$", "Phone"),
    _route(r"^/api/integration-modules/receive-sms/inbound$", "Receive SMS"),
    _route(r"^/api/voice-monitor/provider/callback$", "Voice Monitor"),
    _route(r"^/api/webhooks/smtp2go/events$", "SMTP2Go"),
    _route(r"^/api/integration-modules/uptimekuma/alerts$", "Uptime Kuma"),
    _route(r"^/api/v1/solidtime/webhook$", "Solidtime"),
    _route(r"^/api/integration-modules/trello/webhook$", "Trello"),
    _route(r"^/api/integration-modules/xero/(?:webhook|callback)$", "Xero"),
    _route(r"^/xero/callback$", "Xero OAuth"),
    _route(r"^/m365/callback$", "Microsoft 365 OAuth"),
    _route(r"^/bcp/api/webhook/incident/start$", "BCP Incident"),
    _route(r"^/api/backup-status$", "Backup Status"),
    _route(r"^/api/staff/workflow-webhooks/[^/]+$", "Staff Workflow"),
)

TRAY_PREFIXES = ("/api/tray", "/ws/tray")


@dataclass
class IncomingMonitorContext:
    provider: str
    name: str | None = None
    response_status: int | None = None
    response_body: str | None = None
    error_message: str | None = None


_CURRENT: ContextVar[IncomingMonitorContext | None] = ContextVar(
    "incoming_webhook_monitor", default=None
)


def route_provider(path: str) -> str | None:
    if path.startswith(TRAY_PREFIXES):
        return None
    return next((item.provider for item in INCOMING_ROUTES if item.pattern.fullmatch(path)), None)


def current_context() -> IncomingMonitorContext | None:
    return _CURRENT.get()


def begin_context(provider: str) -> Token[IncomingMonitorContext | None]:
    return _CURRENT.set(IncomingMonitorContext(provider=provider))


def end_context(token: Token[IncomingMonitorContext | None]) -> None:
    _CURRENT.reset(token)


class IncomingWebhookMonitorMiddleware:
    """ASGI middleware recording one event for each registered callback request."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        provider = route_provider(str(scope.get("path") or ""))
        if not provider:
            await self.app(scope, receive, send)
            return

        request_chunks: list[bytes] = []
        response_chunks: list[bytes] = []
        response_status: int | None = None

        # Read once at the ASGI boundary and replay verbatim.  This captures
        # bodies even when a dependency rejects the request before FastAPI
        # constructs/parses the endpoint model, without starving the handler.
        request_messages: list[dict[str, Any]] = []
        while True:
            message = await receive()
            request_messages.append(message)
            if message.get("type") == "http.request":
                request_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                break
        request_index = 0

        async def monitored_receive() -> dict[str, Any]:
            nonlocal request_index
            if request_index < len(request_messages):
                message = request_messages[request_index]
                request_index += 1
                return message
            return await receive()

        async def monitored_send(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
            elif message.get("type") == "http.response.body":
                response_chunks.append(message.get("body", b""))
            await send(message)

        token = begin_context(provider)
        raised: BaseException | None = None
        try:
            await self.app(scope, monitored_receive, monitored_send)
        except BaseException as exc:
            raised = exc
            raise
        finally:
            context = current_context() or IncomingMonitorContext(provider)
            end_context(token)
            await self._record(
                scope=scope,
                context=context,
                request_body=b"".join(request_chunks),
                response_body=b"".join(response_chunks),
                response_status=response_status,
                raised=raised,
            )

    @staticmethod
    async def _record(*, scope: dict[str, Any], context: IncomingMonitorContext,
                      request_body: bytes, response_body: bytes,
                      response_status: int | None, raised: BaseException | None) -> None:
        try:
            from app.services import webhook_monitor

            headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            scheme = str(scope.get("scheme") or "http")
            host = headers.get("host", "localhost")
            path = str(scope.get("path") or "")
            query = bytes(scope.get("query_string") or b"").decode("latin-1")
            url = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
            status = context.response_status or response_status or 500
            error = context.error_message
            if not error and status >= 400:
                error = f"HTTP {status} rejected by endpoint"
            if raised is not None:
                error = f"Internal processing exception ({type(raised).__name__})"
            client = scope.get("client")
            safe_response = context.response_body or sanitise_body(
                response_body, "application/json"
            )
            if safe_response is not None and not isinstance(safe_response, str):
                safe_response = json.dumps(safe_response, default=str)
            await webhook_monitor.log_incoming_webhook(
                name=context.name or f"{context.provider} incoming callback",
                integration=context.provider,
                method=str(scope.get("method") or ""),
                source_url=sanitise_url(url),
                payload=sanitise_body(request_body, headers.get("content-type", "")),
                headers=sanitise_headers(headers),
                source_ip=str(client[0]) if client else None,
                response_status=status,
                response_body=safe_response,
                error_message=error,
            )
        except Exception as exc:  # monitoring must never alter provider responses
            log_warning(
                "Unable to record incoming webhook monitor event",
                provider=context.provider,
                error=type(exc).__name__,
            )
