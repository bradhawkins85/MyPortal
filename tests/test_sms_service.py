import asyncio
from types import SimpleNamespace

import pytest

from app.services import sms as sms_service


def test_send_sms_success(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured["event"] = kwargs
        return {
            "id": 10,
            "status": "succeeded",
            "attempt_count": 1,
        }

    monkeypatch.setattr(sms_service.webhook_monitor, "enqueue_event", fake_enqueue_event)
    monkeypatch.setattr(
        sms_service,
        "get_settings",
        lambda: SimpleNamespace(sms_endpoint="http://example.com/message", sms_auth="dGVzdA=="),
    )

    result = asyncio.run(
        sms_service.send_sms(message="Hello", phone_numbers=["+123", " +123 "])
    )

    assert result is True
    event = captured["event"]
    assert event["target_url"] == "http://example.com/message"
    assert event["payload"]["textMessage"]["text"] == "Hello"
    assert event["payload"]["phoneNumbers"] == ["+123"]
    assert event["headers"]["Authorization"] == "Basic dGVzdA=="
    assert event["backoff_seconds"] == 300


def test_send_sms_skips_without_endpoint(monkeypatch):
    monkeypatch.setattr(
        sms_service,
        "get_settings",
        lambda: SimpleNamespace(sms_endpoint=None, sms_auth="dGVzdA=="),
    )

    result = asyncio.run(
        sms_service.send_sms(message="Hello", phone_numbers=["+123456789"])
    )

    assert result is False


def test_send_sms_raises_on_bad_status(monkeypatch):
    async def fake_enqueue_event(**kwargs):
        return {
            "id": 44,
            "status": "pending",
            "last_error": "HTTP 500",
            "attempt_count": 1,
        }

    monkeypatch.setattr(sms_service.webhook_monitor, "enqueue_event", fake_enqueue_event)
    monkeypatch.setattr(
        sms_service,
        "get_settings",
        lambda: SimpleNamespace(sms_endpoint="http://example.com/message", sms_auth="dGVzdA=="),
    )

    with pytest.raises(sms_service.SMSDispatchError):
        asyncio.run(
            sms_service.send_sms(message="Hello", phone_numbers=["+123456789"])
        )
