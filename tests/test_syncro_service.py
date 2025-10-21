import httpx
import pytest

from app.services import syncro
from app.services.syncro import SyncroAPIError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def reset_syncro_caches(monkeypatch):
    monkeypatch.setattr(syncro, "_MODULE_SETTINGS_CACHE", None)
    monkeypatch.setattr(syncro, "_MODULE_SETTINGS_EXPIRY", 0.0)
    monkeypatch.setattr(syncro, "_RATE_LIMITER_CACHE", None)
    return monkeypatch


@pytest.mark.anyio
async def test_syncro_request_records_webhook_success(reset_syncro_caches):
    recorded: dict[str, list[dict[str, object]] | dict[str, object]] = {"success": []}

    async def fake_get_module(slug: str):
        assert slug == "syncro"
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://syncro.example",
                "api_key": "secret",
                "rate_limit_per_minute": 100,
            },
        }

    async def fake_manual_event(**kwargs):
        recorded["enqueue"] = kwargs
        return {"id": 321}

    async def fake_record_success(event_id: int, *, attempt_number: int, response_status: int | None, response_body: str | None):
        recorded.setdefault("success", []).append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )
        return {"id": event_id, "status": "succeeded"}

    async def fake_record_failure(event_id: int, *, attempt_number: int, status: str, error_message: str | None, response_status: int | None, response_body: str | None):
        recorded.setdefault("failure", []).append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "status": status,
                "error_message": error_message,
                "response_status": response_status,
                "response_body": response_body,
            }
        )
        return {"id": event_id, "status": "failed"}

    class DummyResponse:
        status_code = 200

        def __init__(self):
            self._text = "{\"tickets\": []}"

        @property
        def text(self) -> str:
            return self._text

        def json(self) -> dict[str, object]:
            return {"tickets": []}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None, params=None, json=None):
            recorded["request"] = {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
            }
            return DummyResponse()

    reset_syncro_caches.setattr(syncro.module_repo, "get_module", fake_get_module)
    reset_syncro_caches.setattr(syncro.webhook_monitor, "create_manual_event", fake_manual_event)
    reset_syncro_caches.setattr(syncro.webhook_monitor, "record_manual_success", fake_record_success)
    reset_syncro_caches.setattr(syncro.webhook_monitor, "record_manual_failure", fake_record_failure)
    reset_syncro_caches.setattr(syncro.httpx, "AsyncClient", DummyClient)

    payload = await syncro._request("GET", "/tickets", params={"page": 1})

    assert payload == {"tickets": []}
    assert recorded["enqueue"]["target_url"] == "https://syncro.example/api/v1/tickets"
    assert recorded["enqueue"]["payload"] == {"method": "GET", "params": {"page": 1}}
    assert recorded["request"]["headers"]["Authorization"].startswith("Bearer ")
    assert recorded["success"][0]["event_id"] == 321
    assert recorded["success"][0]["response_status"] == 200
    assert recorded.get("failure") is None


@pytest.mark.anyio
async def test_syncro_request_records_webhook_failure(reset_syncro_caches):
    recorded: dict[str, list[dict[str, object]] | dict[str, object]] = {}

    async def fake_get_module(slug: str):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://syncro.example",
                "api_key": "secret",
                "rate_limit_per_minute": 60,
            },
        }

    async def fake_manual_event(**kwargs):
        recorded["enqueue"] = kwargs
        return {"id": 654}

    async def fake_record_failure(event_id: int, *, attempt_number: int, status: str, error_message: str | None, response_status: int | None, response_body: str | None):
        recorded.setdefault("failure", []).append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "status": status,
                "error_message": error_message,
                "response_status": response_status,
                "response_body": response_body,
            }
        )
        return {"id": event_id, "status": status}

    async def fake_record_success(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("record_manual_success should not be invoked on failure")

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None, params=None, json=None):
            raise httpx.HTTPError("boom")

    reset_syncro_caches.setattr(syncro.module_repo, "get_module", fake_get_module)
    reset_syncro_caches.setattr(syncro.webhook_monitor, "create_manual_event", fake_manual_event)
    reset_syncro_caches.setattr(syncro.webhook_monitor, "record_manual_failure", fake_record_failure)
    reset_syncro_caches.setattr(syncro.webhook_monitor, "record_manual_success", fake_record_success)
    reset_syncro_caches.setattr(syncro.httpx, "AsyncClient", FailingClient)

    with pytest.raises(SyncroAPIError):
        await syncro._request("GET", "/tickets/1")

    assert recorded["enqueue"]["target_url"] == "https://syncro.example/api/v1/tickets/1"
    assert recorded["failure"][0]["event_id"] == 654
    assert recorded["failure"][0]["status"] == "error"
    assert recorded["failure"][0]["error_message"] == "boom"
