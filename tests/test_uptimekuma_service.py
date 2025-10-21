import hashlib
from datetime import datetime, timezone

import pytest

from app.schemas.uptimekuma import UptimeKumaAlertPayload
from app.services import uptimekuma as uptime_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_persists(monkeypatch):
    secret_hash = hashlib.sha256("secret-token".encode("utf-8")).hexdigest()
    captured: dict[str, object] = {}

    async def fake_get_module(slug: str, *, redact: bool = True):
        assert slug == "uptimekuma"
        assert redact is False
        return {"enabled": True, "settings": {"shared_secret_hash": secret_hash}}

    async def fake_create_alert(**kwargs):
        captured.update(kwargs)
        return {"id": 99, **kwargs}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)

    payload = UptimeKumaAlertPayload(
        status="down",
        monitor_id=7,
        monitor_name="API",
        monitor_url="https://status.example.com",
        time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        importance=True,
        previous_status="up",
        duration=42.5,
        ping=130,
    )

    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload=payload.model_dump(mode="json", by_alias=True),
        provided_secret="secret-token",
        remote_addr="203.0.113.5",
        user_agent="uptime-kuma",
    )

    assert record["id"] == 99
    assert captured["status"] == "down"
    assert captured["importance"] is True
    assert captured["remote_addr"] == "203.0.113.5"
    assert captured["occurred_at"].tzinfo == timezone.utc


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_rejects_missing_secret(monkeypatch):
    secret_hash = hashlib.sha256("secret-token".encode("utf-8")).hexdigest()

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "settings": {"shared_secret_hash": secret_hash}}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)

    payload = UptimeKumaAlertPayload(status="up")

    with pytest.raises(uptime_service.AuthenticationError):
        await uptime_service.ingest_alert(
            payload=payload,
            raw_payload={},
            provided_secret=None,
            remote_addr=None,
            user_agent=None,
        )


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_disabled_module(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": False, "settings": {}}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)

    payload = UptimeKumaAlertPayload(status="up")

    with pytest.raises(uptime_service.ModuleDisabledError):
        await uptime_service.ingest_alert(
            payload=payload,
            raw_payload={},
            provided_secret=None,
            remote_addr=None,
            user_agent=None,
        )
