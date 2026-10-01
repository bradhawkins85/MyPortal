from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import monitored_http


@pytest.fixture
def repo(monkeypatch):
    create = AsyncMock(return_value={"id": 42})
    progress = AsyncMock()
    attempt = AsyncMock()
    completed = AsyncMock()
    failed = AsyncMock()
    monkeypatch.setattr(monitored_http.webhook_repo, "create_event", create)
    monkeypatch.setattr(monitored_http.webhook_repo, "mark_in_progress", progress)
    monkeypatch.setattr(monitored_http.webhook_repo, "record_attempt", attempt)
    monkeypatch.setattr(monitored_http.webhook_repo, "mark_event_completed", completed)
    monkeypatch.setattr(monitored_http.webhook_repo, "mark_event_failed", failed)
    return create, progress, attempt, completed, failed


@pytest.mark.anyio("asyncio")
async def test_success_is_created_before_send_and_sanitised(repo):
    create, progress, attempt, completed, failed = repo

    async def handler(request):
        assert create.await_count == 1
        assert progress.await_count == 1
        return httpx.Response(201, headers={"Set-Cookie": "session=secret"}, json={"token": "response-secret", "ok": True})

    async with monitored_http.MonitoredAsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat?api_key=query-secret&model=x",
            headers={"Authorization": "Bearer header-secret"},
            json={"password": "body-secret", "prompt": "hello"},
        )

    assert response.status_code == 201
    event = create.await_args.kwargs
    assert event["direction"] == "outgoing"
    assert event["name"] == "OpenAI"
    assert "query-secret" not in event["target_url"]
    assert event["headers"]["authorization"] == monitored_http.REDACTED
    assert event["payload"]["password"] == monitored_http.REDACTED
    result = attempt.await_args.kwargs
    assert result["status"] == "succeeded"
    assert result["response_headers"]["set-cookie"] == monitored_http.REDACTED
    assert "response-secret" not in result["response_body"]
    completed.assert_awaited_once()
    failed.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_non_2xx_is_recorded_as_failure(repo):
    *_, attempt, completed, failed = repo
    async with monitored_http.MonitoredAsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="unavailable"))
    ) as client:
        response = await client.get("https://status.example.test/check")
    assert response.status_code == 503
    assert attempt.await_args.kwargs["error_message"] == "HTTP 503"
    completed.assert_not_awaited()
    failed.assert_awaited_once()


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("error, expected", [
    (httpx.ReadTimeout("too slow"), "timeout"),
    (httpx.ConnectError("connection failed"), "failed"),
    (RuntimeError("unexpected"), "failed"),
])
async def test_transport_exceptions_are_recorded_and_re_raised(repo, error, expected):
    *_, attempt, _, failed = repo

    async def handler(request):
        raise error

    async with monitored_http.MonitoredAsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(type(error)):
            await client.get("https://provider.test/path?token=do-not-log")
    result = attempt.await_args.kwargs
    assert result["status"] == expected
    assert "do-not-log" not in result["error_message"]
    failed.assert_awaited_once()


def test_redaction_and_documented_truncation_limit():
    body = monitored_http.sanitise_body(
        '{"client_secret":"hidden","nested":{"access_token":"hidden"},"data":"' +
        ("x" * monitored_http.MONITOR_BODY_LIMIT) + '"}',
        "application/json",
    )
    assert isinstance(body, str)
    assert len(body) == monitored_http.MONITOR_BODY_LIMIT
    assert "hidden" not in body
    assert body.endswith("...")


@pytest.mark.anyio("asyncio")
async def test_tray_agent_can_be_explicitly_excluded(repo):
    create, progress, attempt, completed, failed = repo
    async with monitored_http.MonitoredAsyncClient(
        monitor=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    ) as client:
        response = await client.post("https://tray-agent.internal/action")
    assert response.status_code == 204
    for mock in (create, progress, attempt, completed, failed):
        mock.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_monitor_database_failure_does_not_change_response(repo):
    create, *_ = repo
    create.side_effect = RuntimeError("database unavailable")
    async with monitored_http.MonitoredAsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    ) as client:
        response = await client.get("https://provider.test")
    assert response.text == "ok"
