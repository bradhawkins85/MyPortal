from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import incoming_webhooks, webhook_monitor
from app.services.monitored_http import MONITOR_BODY_LIMIT, REDACTED


def _scope(path: str, *, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "query_string": b"token=query-secret",
        "headers": headers or [(b"host", b"portal.example"), (b"content-type", b"application/json")],
        "client": ("203.0.113.8", 1234),
    }


async def _invoke(middleware, scope, body: bytes):
    incoming = [{"type": "http.request", "body": body, "more_body": False}]
    outgoing = []

    async def receive():
        return incoming.pop(0)

    async def send(message):
        outgoing.append(message)

    await middleware(scope, receive, send)
    return outgoing


@pytest.mark.asyncio
async def test_authentication_rejection_is_logged_once_and_redacted(monkeypatch):
    recorded = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", recorded)

    async def rejected(scope, receive, send):
        # Simulates authentication failing before an endpoint reads its model.
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b'{"detail":"invalid key"}'})

    middleware = incoming_webhooks.IncomingWebhookMonitorMiddleware(rejected)
    await _invoke(
        middleware,
        _scope(
            "/api/integration-modules/receive-sms/inbound",
            headers=[
                (b"host", b"portal.example"),
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer top-secret"),
            ],
        ),
        b'{"from_number":"0400000000","message":"private text","token":"body-secret"}',
    )

    recorded.assert_awaited_once()
    values = recorded.await_args.kwargs
    assert values["integration"] == "Receive SMS"
    assert values["method"] == "POST"
    assert values["response_status"] == 401
    assert "query-secret" not in values["source_url"]
    assert values["headers"]["authorization"] == REDACTED
    assert values["payload"]["from_number"] == REDACTED
    assert values["payload"]["message"] == REDACTED
    assert values["payload"]["token"] == REDACTED


@pytest.mark.asyncio
async def test_manual_endpoint_logging_enriches_but_does_not_duplicate(monkeypatch):
    from app.repositories import webhook_events

    create = AsyncMock(return_value={"id": 17})
    monkeypatch.setattr(webhook_events, "create_event", create)
    monkeypatch.setattr(webhook_events, "record_attempt", AsyncMock())
    monkeypatch.setattr(webhook_events, "mark_event_failed", AsyncMock())
    monkeypatch.setattr(webhook_events, "get_event", AsyncMock(return_value={"id": 17}))

    async def endpoint(scope, receive, send):
        await webhook_monitor.log_incoming_webhook(
            name="Trello Webhook - Invalid JSON",
            source_url="https://portal.example/api/integration-modules/trello/webhook",
            response_status=400,
            response_body="Invalid JSON payload",
            error_message="Invalid JSON payload",
        )
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b'{"detail":"Invalid JSON payload"}'})

    middleware = incoming_webhooks.IncomingWebhookMonitorMiddleware(endpoint)
    await _invoke(middleware, _scope("/api/integration-modules/trello/webhook"), b"{")

    create.assert_awaited_once()
    assert create.await_args.kwargs["name"] == "Trello Webhook - Invalid JSON"


@pytest.mark.asyncio
async def test_processing_exception_is_logged_without_leaking_exception_text(monkeypatch):
    recorded = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", recorded)

    async def broken(scope, receive, send):
        raise RuntimeError("database password was secret")

    middleware = incoming_webhooks.IncomingWebhookMonitorMiddleware(broken)
    with pytest.raises(RuntimeError):
        await _invoke(middleware, _scope("/api/voice-monitor/provider/callback"), b"{}")
    error = recorded.await_args.kwargs["error_message"]
    assert error == "Internal processing exception (RuntimeError)"
    assert "password" not in error


def test_registered_routes_and_tray_exclusion_are_explicit():
    expected = {
        "/phonewebhook/token/": "Phone",
        "/api/integration-modules/receive-sms/inbound": "Receive SMS",
        "/api/voice-monitor/provider/callback": "Voice Monitor",
        "/api/webhooks/smtp2go/events": "SMTP2Go",
        "/api/integration-modules/uptimekuma/alerts": "Uptime Kuma",
        "/api/v1/solidtime/webhook": "Solidtime",
        "/api/integration-modules/trello/webhook": "Trello",
        "/api/integration-modules/xero/webhook": "Xero",
        "/bcp/api/webhook/incident/start": "BCP Incident",
        "/api/backup-status": "Backup Status",
        "/api/staff/workflow-webhooks/id": "Staff Workflow",
    }
    assert {path: incoming_webhooks.route_provider(path) for path in expected} == expected
    assert incoming_webhooks.route_provider("/api/tray/enrol") is None
    assert incoming_webhooks.route_provider("/ws/tray/device") is None


def test_body_limit_is_documented_and_applied():
    body = incoming_webhooks.sanitise_body("x" * (MONITOR_BODY_LIMIT + 50))
    assert len(body) == MONITOR_BODY_LIMIT
    assert body.endswith("...")
