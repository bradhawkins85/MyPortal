"""Retrying a webhook must not replay stale transport headers or shortened bodies."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import webhook_monitor
from app.services.monitored_http import MONITOR_BODY_LIMIT


@pytest.mark.asyncio
async def test_retry_drops_transport_headers_from_original_attempt():
    captured: dict = {}
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.text = "{}"
    response.headers = {}

    async def fake_post(url, **kwargs):
        captured.update(kwargs)
        return response

    event = {
        "id": 7,
        "name": "Example",
        "target_url": "http://172.16.254.155:8484/hook",
        "headers": {
            "host": "172.16.254.155:8484",
            "content-length": "94348",
            "connection": "keep-alive",
            "content-type": "application/json",
            "X-Custom": "kept",
        },
        "payload": {"ok": True},
    }
    with patch("app.services.webhook_monitor.webhook_repo") as repo, \
         patch("app.services.webhook_monitor.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = fake_post
        client_cls.return_value = client
        repo.record_attempt = AsyncMock()
        repo.mark_event_completed = AsyncMock()
        await webhook_monitor._attempt_event(event)

    sent = {key.lower() for key in captured["headers"]}
    assert sent == {"content-type", "x-custom"}


def test_truncated_llm_body_is_not_replayable():
    shortened = ("{\"model\": \"gemma4:e2b\", \"messages\": " + "x" * MONITOR_BODY_LIMIT)[
        : MONITOR_BODY_LIMIT - 3
    ] + "..."
    assert webhook_monitor.replay_blocker({"payload": shortened})
    assert webhook_monitor.replay_blocker({"payload": {"a": 1}, "metadata": {"replayable": False}})
    assert webhook_monitor.replay_blocker({"payload": {"a": 1}, "metadata": {"http_method": "POST"}}) is None


@pytest.mark.asyncio
async def test_force_retry_refuses_shortened_body_without_sending():
    event = {"id": 9, "payload": "x" * (MONITOR_BODY_LIMIT - 3) + "..."}
    with patch("app.services.webhook_monitor.webhook_repo") as repo, \
         patch("app.services.webhook_monitor._attempt_event", new=AsyncMock()) as attempt:
        repo.get_event = AsyncMock(return_value=event)
        repo.force_retry = AsyncMock()
        with pytest.raises(webhook_monitor.WebhookNotReplayableError):
            await webhook_monitor.force_retry(9)
    repo.force_retry.assert_not_awaited()
    attempt.assert_not_awaited()
