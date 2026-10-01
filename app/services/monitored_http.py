"""HTTPX client which records every third-party request in Webhook Monitor.

Bodies stored by this module are limited to :data:`MONITOR_BODY_LIMIT` characters.
Query-string values and fields whose names commonly contain credentials are always
redacted.  Monitoring is deliberately best-effort: a database problem must never
change the result of the integration request.

Calls to the MyPortal Tray agent must opt out with ``monitor=False`` (or the
``myportal.skip_monitor`` request extension).  This explicit marker avoids unsafe
host-name guesses while making the exclusion reviewable.
"""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.core.logging import log_warning
from app.repositories import webhook_events as webhook_repo

MONITOR_BODY_LIMIT = 4_000
REDACTED = "***REDACTED***"
_HTTPX_ASYNC_CLIENT = httpx.AsyncClient
_SENSITIVE_NAMES = {
    "authorization", "cookie", "set-cookie", "proxy-authorization",
    "api-key", "apikey", "api_key", "x-api-key", "x-auth-token",
    "access_token", "refresh_token", "id_token", "token", "secret",
    "client_secret", "password", "passwd", "signature", "sig", "key",
    # Common personal data in inbound communication/provider payloads.
    "email", "email_address", "phone", "phone_number", "mobile",
    "mobile_phone", "from", "from_number", "sender", "recipient",
    "address", "name", "message",
}


def _is_sensitive(name: object) -> bool:
    value = str(name).lower().replace("-", "_")
    return value in {item.replace("-", "_") for item in _SENSITIVE_NAMES} or any(
        marker in value for marker in ("password", "secret", "token", "credential")
    )


def _truncate(value: str | None) -> str | None:
    if value is None or len(value) <= MONITOR_BODY_LIMIT:
        return value
    return value[: MONITOR_BODY_LIMIT - 3] + "..."


def sanitise_url(url: str) -> str:
    """Remove user info, fragments, and all query values (including signed URLs)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host + (f":{parts.port}" if parts.port else "")
        query = urlencode([(key, REDACTED) for key, _ in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except (TypeError, ValueError):
        return "invalid-url"


def sanitise_headers(headers: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not headers:
        return None
    return {str(key): REDACTED if _is_sensitive(key) else _truncate(str(value)) or "" for key, value in headers.items()}


def _redact_value(value: Any, key: object | None = None) -> Any:
    if key is not None and _is_sensitive(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): _redact_value(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def sanitise_body(content: bytes | str | None, content_type: str = "") -> Any:
    if not content:
        return None
    text = content.decode(errors="replace") if isinstance(content, bytes) else content
    try:
        if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
            redacted = _redact_value(json.loads(text))
            serialised = json.dumps(redacted, default=str)
            return redacted if len(serialised) <= MONITOR_BODY_LIMIT else _truncate(serialised)
        if "application/x-www-form-urlencoded" in content_type.lower():
            return _truncate(urlencode([(k, REDACTED if _is_sensitive(k) else v) for k, v in parse_qsl(text, keep_blank_values=True)]))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return _truncate(text)


def _provider(url: httpx.URL) -> str:
    host = (url.host or "third-party").lower()
    known = {
        "graph.microsoft.com": "Microsoft Graph", "login.microsoftonline.com": "Microsoft OAuth",
        "api.openai.com": "OpenAI", "api.github.com": "GitHub", "smtp2go.com": "SMTP2Go",
        "api.trello.com": "Trello", "api.xero.com": "Xero",
    }
    return known.get(host, host.removeprefix("api.") or "Third-party HTTP")


class MonitoredAsyncClient(httpx.AsyncClient):
    """Drop-in ``httpx.AsyncClient`` with non-intrusive Webhook Monitor logging."""

    def __init__(self, *args: Any, monitor: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._monitor = monitor

    async def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        if not self._monitor or request.extensions.get("myportal.skip_monitor"):
            return await super().send(request, *args, **kwargs)

        event_id: int | None = None
        safe_url = sanitise_url(str(request.url))
        safe_headers = sanitise_headers(request.headers)
        try:
            content = request.content
        except httpx.RequestNotRead:
            content = None
        safe_body = sanitise_body(content, request.headers.get("content-type", ""))
        try:
            metadata: dict[str, Any] = {"http_method": request.method}
            if request.extensions.get("myportal.operation_id"):
                metadata["operation_id"] = str(request.extensions["myportal.operation_id"])
            if request.extensions.get("myportal.attempt"):
                metadata["attempt"] = int(request.extensions["myportal.attempt"])
            event = await webhook_repo.create_event(
                name=_provider(request.url), target_url=safe_url, headers=safe_headers,
                payload=safe_body, max_attempts=1, backoff_seconds=1, direction="outgoing",
                metadata=metadata,
            )
            if event.get("id") is not None:
                event_id = int(event["id"])
                await webhook_repo.mark_in_progress(event_id)
        except Exception as exc:  # monitoring cannot break integrations
            log_warning("Unable to create outgoing HTTP monitor event", error=type(exc).__name__)

        try:
            response = await super().send(request, *args, **kwargs)
        except Exception as exc:
            await self._finish(event_id, request, None, exc, safe_headers, safe_body)
            raise
        await self._finish(event_id, request, response, None, safe_headers, safe_body)
        return response

    async def _finish(self, event_id: int | None, request: httpx.Request,
                      response: httpx.Response | None, error: BaseException | None,
                      request_headers: dict[str, str] | None, request_body: Any) -> None:
        if event_id is None:
            return
        status_code = response.status_code if response is not None else None
        success = error is None and status_code is not None and 200 <= status_code < 300
        status = "succeeded" if success else ("timeout" if isinstance(error, httpx.TimeoutException) else "failed")
        try:
            response_body = None
            if response is not None:
                try:
                    response_body = sanitise_body(response.content, response.headers.get("content-type", ""))
                except httpx.ResponseNotRead:
                    response_body = "<streaming response>"
                if not isinstance(response_body, str):
                    response_body = _truncate(json.dumps(response_body, default=str))
            message = None if success else (
                f"{type(error).__name__}: request to {sanitise_url(str(request.url))} failed"
                if error is not None else f"HTTP {status_code}"
            )
            await webhook_repo.record_attempt(
                event_id=event_id, attempt_number=1, status=status,
                response_status=status_code, response_body=response_body, error_message=message,
                request_headers=request_headers, request_body=request_body,
                response_headers=sanitise_headers(response.headers) if response is not None else None,
            )
            marker = webhook_repo.mark_event_completed if success else webhook_repo.mark_event_failed
            if success:
                await marker(event_id, attempt_number=1, response_status=status_code, response_body=response_body)
            else:
                await marker(event_id, attempt_number=1, error_message=message,
                             response_status=status_code, response_body=response_body)
        except Exception as exc:
            log_warning("Unable to finish outgoing HTTP monitor event", event_id=event_id, error=type(exc).__name__)


def monitored_client(client_class: type[httpx.AsyncClient], *args: Any, **kwargs: Any) -> httpx.AsyncClient:
    """Build a monitored client while retaining integration-test substitution.

    Callers pass their module's ``httpx.AsyncClient`` reference.  Production
    therefore receives :class:`MonitoredAsyncClient`; tests and plugins which
    explicitly substitute that reference continue to receive their stand-in.
    """
    if client_class is _HTTPX_ASYNC_CLIENT:
        return MonitoredAsyncClient(*args, **kwargs)
    return client_class(*args, **kwargs)
