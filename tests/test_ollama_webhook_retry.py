"""Retrying a module.ollama.* webhook event must replay a valid provider request."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import webhook_monitor


async def _retry(event: dict, settings: dict | None) -> dict:
    captured: dict = {}
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.text = "{}"
    response.headers = {}

    async def fake_post(url, **kwargs):
        captured.update(kwargs)
        return response

    with patch("app.services.webhook_monitor.webhook_repo") as repo, \
         patch("app.services.webhook_monitor.httpx.AsyncClient") as client_cls, \
         patch("app.services.modules.get_module_settings", new=AsyncMock(return_value=settings)):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = fake_post
        client_cls.return_value = client
        repo.record_attempt = AsyncMock()
        repo.mark_event_completed = AsyncMock()
        await webhook_monitor._attempt_event(event)
    return captured


@pytest.mark.asyncio
async def test_retry_unwraps_legacy_request_body_wrapper():
    body = {"model": "gemma4:e2b", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    event = {
        "id": 1,
        "name": "module.ollama.llamacpp.generate",
        "target_url": "http://llama.local/v1/chat/completions",
        "headers": {"Content-Type": "application/json"},
        "payload": {"request_body": body},
    }
    captured = await _retry(event, {"api_key": ""})
    assert captured["json"] == body
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_retry_restores_redacted_authorization_from_settings():
    body = {"model": "gpt-4o-mini", "messages": [], "stream": False}
    event = {
        "id": 2,
        "name": "module.ollama.openai.generate",
        "target_url": "https://api.openai.com/v1/chat/completions",
        "headers": {"Content-Type": "application/json", "Authorization": "***REDACTED***"},
        "payload": body,
    }
    captured = await _retry(event, {"api_key": "sk-test"})
    assert captured["json"] == body
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
