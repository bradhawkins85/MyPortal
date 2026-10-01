"""Tests for SMTP2Go analytics, retry, and engagement automation helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import smtp2go


def test_extract_engagement_dimensions_from_webhook_metadata():
    dimensions = smtp2go.extract_engagement_dimensions(
        {
            "city": "Brisbane",
            "region": "QLD",
            "country": "AU",
            "client_name": "Outlook",
        },
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile",
    )

    assert dimensions["geo"] == "Brisbane, QLD, AU"
    assert dimensions["device"] == "mobile"
    assert dimensions["client"] == "Outlook"


def test_select_ab_test_winner_uses_best_metric_with_sample_threshold():
    summary = smtp2go.select_ab_test_winner(
        {
            "name": "Onboarding nurture",
            "winner_metric": "click_rate",
            "minimum_sample_size": 10,
            "variants": [
                {"name": "A", "sent": 12, "clicked": 2},
                {"name": "B", "sent": 12, "clicked": 4},
            ],
        }
    )

    assert summary["status"] == "winner_selected"
    assert summary["winner"]["name"] == "B"
    assert summary["winner"]["metrics"]["click_rate"] > summary["variants"][0]["metrics"]["click_rate"]


@pytest.mark.asyncio
async def test_send_email_via_api_retries_after_rate_limit(monkeypatch):
    calls: list[int] = []
    sleeps: list[int] = []

    class RateLimitedResponse:
        status_code = 429
        headers = {"Retry-After": "2"}
        text = "Too many requests"

        def raise_for_status(self):
            import httpx

            raise httpx.HTTPStatusError(
                "rate limited",
                request=httpx.Request("POST", "https://api.smtp2go.com/v3/email/send"),
                response=httpx.Response(429),
            )

        def json(self):
            return {"error": "rate limited"}

    class SuccessResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"error_code": "SUCCESS", "email_id": "msg-123"}}

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            calls.append(len(calls) + 1)
            return RateLimitedResponse() if len(calls) == 1 else SuccessResponse()

    async def fake_get_module_settings(slug):
        return {
            "api_key": "test-api-key",
            "rate_limit_max_retries": 2,
            "retry_backoff_seconds": 5,
        }

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def fake_require_module_enabled(slug):
        assert slug == "smtp2go"

    async def fake_filter_allowed(addresses):
        return list(addresses), []

    import httpx
    from app.repositories import email_blocklist
    from app.services import modules as modules_service

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: MockAsyncClient())
    monkeypatch.setattr(modules_service, "get_module_settings", fake_get_module_settings)
    monkeypatch.setattr(smtp2go, "require_module_enabled", fake_require_module_enabled)
    monkeypatch.setattr(smtp2go.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(email_blocklist, "filter_allowed", fake_filter_allowed)

    result = await smtp2go.send_email_via_api(
        to=["customer@example.com"],
        subject="Retry me",
        html_body="<p>Hello</p>",
        sender="sender@example.com",
    )

    assert calls == [1, 2]
    assert sleeps == [2]
    assert result["delivery_queue"]["attempt_count"] == 2
    assert result["delivery_queue"]["retry_history"][0]["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_process_webhook_event_triggers_open_automation(monkeypatch):
    class DummyDb:
        async def fetch_one(self, query, params):
            if "FROM ticket_replies" in query:
                return {"id": 7, "email_tracking_id": "track-1"}
            return None

        async def execute(self, query, params):
            return 99

    from app.services import email_recipients

    automation = AsyncMock(return_value=[{"automation_id": 1, "status": "queued"}])
    monkeypatch.setattr(smtp2go, "db", DummyDb())
    monkeypatch.setattr(smtp2go, "_trigger_engagement_automation", automation)
    monkeypatch.setattr(email_recipients, "update_recipient_event", AsyncMock(return_value=1))

    result = await smtp2go.process_webhook_event(
        "open",
        {
            "email_id": "msg-1",
            "rcpt": "person@example.com",
            "timestamp": "2026-09-15T10:00:00Z",
            "user-agent": "Outlook-iOS/1.0",
        },
    )

    assert result["event_type"] == "open"
    automation.assert_awaited_once()
    assert automation.await_args.args[0] == "opened"


@pytest.mark.asyncio
async def test_process_webhook_event_schedules_not_engaged_follow_up(monkeypatch):
    class DummyDb:
        async def fetch_one(self, query, params):
            if "FROM ticket_replies" in query:
                return {"id": 7, "email_tracking_id": "track-1"}
            return None

        async def execute(self, query, params):
            return 100

    from app.services import email_recipients
    from app.services import modules as modules_service

    schedule = AsyncMock(return_value=True)

    async def fake_get_module_settings(slug):
        assert slug == "smtp2go"
        return {"not_engaged_delay_seconds": 60}

    monkeypatch.setattr(smtp2go, "db", DummyDb())
    monkeypatch.setattr(modules_service, "get_module_settings", fake_get_module_settings)
    monkeypatch.setattr(smtp2go, "_schedule_not_engaged_follow_up", schedule)
    monkeypatch.setattr(email_recipients, "update_recipient_event", AsyncMock(return_value=1))

    result = await smtp2go.process_webhook_event(
        "delivered",
        {
            "email_id": "msg-1",
            "rcpt": "person@example.com",
            "timestamp": "2026-09-15T10:00:00Z",
        },
    )

    assert result["event_type"] == "delivered"
    schedule.assert_awaited_once()
    assert schedule.await_args.kwargs["delay_seconds"] == 60
