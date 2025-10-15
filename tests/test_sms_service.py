import asyncio
from types import SimpleNamespace

import pytest

from app.services import sms as sms_service


def test_send_sms_success(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status_code: int = 200, text: str = "OK") -> None:
            self.status_code = status_code
            self.text = text

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["timeout"] = kwargs.get("timeout") or (args[0] if args else None)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # type: ignore[override]
            return False

        async def post(self, url: str, json: dict, headers: dict):
            captured["request"] = {"url": url, "json": json, "headers": headers}
            return DummyResponse()

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(
        sms_service,
        "get_settings",
        lambda: SimpleNamespace(sms_endpoint="http://example.com/message", sms_auth="dGVzdA=="),
    )

    result = asyncio.run(
        sms_service.send_sms(message="Hello", phone_numbers=["+123", " +123 "])
    )

    assert result is True
    request = captured["request"]
    assert request["url"] == "http://example.com/message"
    assert request["json"]["textMessage"]["text"] == "Hello"
    assert request["json"]["phoneNumbers"] == ["+123"]
    assert request["headers"]["Authorization"] == "Basic dGVzdA=="
    assert captured["timeout"] == 10.0


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
    class DummyResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.text = "Error"

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # type: ignore[override]
            return False

        async def post(self, url: str, json: dict, headers: dict):
            return DummyResponse(500)

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(
        sms_service,
        "get_settings",
        lambda: SimpleNamespace(sms_endpoint="http://example.com/message", sms_auth="dGVzdA=="),
    )

    with pytest.raises(sms_service.SMSDispatchError):
        asyncio.run(
            sms_service.send_sms(message="Hello", phone_numbers=["+123456789"])
        )
