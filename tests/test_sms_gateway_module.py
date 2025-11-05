import asyncio
import json

import httpx
import pytest

from app.services import modules


async def _noop(*args, **kwargs):
    return None


class _AsyncClientFactory:
    def __init__(self, response):
        self._response = response
        self.captured_kwargs: dict[str, object] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, data=None, headers=None):
        self.captured_kwargs = {
            "url": url,
            "json": json,
            "data": data,
            "headers": headers,
        }
        return self._response


class FakeResponse:
    def __init__(self, status_code=200, response_text='{"status": "sent"}'):
        self.status_code = status_code
        self._text = response_text
        self.request = httpx.Request("POST", "http://example.com")

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "HTTP error",
                request=self.request,
                response=self,
            )
        return None


def test_invoke_sms_gateway_success(monkeypatch):
    captured_event: dict[str, object] = {}

    async def fake_create_manual_event(**kwargs):
        captured_event.update(kwargs)
        return {"id": 1, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 1,
        "status": "pending",
        "attempt_count": 0,
    }
    attempts: list[dict[str, object]] = []

    async def fake_record_attempt(**kwargs):
        attempts.append(kwargs)

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_sms_gateway(
            {
                "gateway_url": "https://sms.example.com/api/send",
                "authorization": "Bearer test-token",
            },
            {
                "message": "Test SMS message",
                "phoneNumbers": ["+1234567890", "+0987654321"],
            },
        )
    )

    assert result["event_id"] == 1
    assert result["status"] == "succeeded"
    assert result["phone_count"] == 2
    assert captured_event.get("name") == "module.sms-gateway.send"
    assert captured_event.get("target_url") == "https://sms.example.com/api/send"
    assert client_factory.captured_kwargs["json"]["message"] == "Test SMS message"
    assert client_factory.captured_kwargs["json"]["phoneNumbers"] == ["+1234567890", "+0987654321"]
    assert client_factory.captured_kwargs["headers"]["authorization"] == "Bearer test-token"


def test_invoke_sms_gateway_missing_url(monkeypatch):
    async def fake_create_manual_event(**kwargs):
        return {"id": 1, "status": "pending"}

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)

    with pytest.raises(ValueError, match="SMS Gateway URL is not configured"):
        asyncio.run(
            modules._invoke_sms_gateway(
                {"gateway_url": "", "authorization": "Bearer test-token"},
                {"message": "Test", "phoneNumbers": ["+1234567890"]},
            )
        )


def test_invoke_sms_gateway_missing_authorization(monkeypatch):
    async def fake_create_manual_event(**kwargs):
        return {"id": 1, "status": "pending"}

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)

    with pytest.raises(ValueError, match="SMS Gateway authorization is not configured"):
        asyncio.run(
            modules._invoke_sms_gateway(
                {"gateway_url": "https://sms.example.com/api/send", "authorization": ""},
                {"message": "Test", "phoneNumbers": ["+1234567890"]},
            )
        )


def test_update_module_preserves_sms_gateway_authorization_when_blank(monkeypatch):
    stored = {
        "slug": "sms-gateway",
        "enabled": True,
        "settings": {
            "gateway_url": "https://sms.example.com/api/send",
            "authorization": "Bearer existing-token",
        },
    }
    captured: dict[str, object] = {}

    async def fake_get_module(slug: str):
        assert slug == "sms-gateway"
        return stored

    async def fake_update_module(slug: str, *, enabled=None, settings=None):
        assert slug == "sms-gateway"
        captured.update(settings or {})
        if settings:
            stored["settings"].update(settings)
        if enabled is not None:
            stored["enabled"] = enabled
        return {"slug": slug, "enabled": stored["enabled"], "settings": dict(stored["settings"])}

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules.module_repo, "update_module", fake_update_module)

    result = asyncio.run(
        modules.update_module(
            "sms-gateway",
            enabled=True,
            settings={"gateway_url": "https://sms.example.com/api/v2/send", "authorization": ""},
        )
    )

    assert captured["authorization"] == "Bearer existing-token"
    assert result["settings"]["authorization"] == "********"


def test_list_modules_redacts_sms_gateway_authorization(monkeypatch):
    async def fake_list_modules():
        return [
            {
                "slug": "sms-gateway",
                "enabled": True,
                "settings": {
                    "gateway_url": "https://sms.example.com/api/send",
                    "authorization": "Bearer secret-token",
                },
            },
        ]

    monkeypatch.setattr(modules.module_repo, "list_modules", fake_list_modules)

    result = asyncio.run(modules.list_modules())

    assert len(result) == 1
    assert result[0]["slug"] == "sms-gateway"
    assert result[0]["settings"]["authorization"] == "********"
    assert result[0]["settings"]["gateway_url"] == "https://sms.example.com/api/send"
