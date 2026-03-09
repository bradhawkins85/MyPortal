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

    async def fake_find_service_by_name(name: str):
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

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


# ---------------------------------------------------------------------------
# Apprise format parsing helpers
# ---------------------------------------------------------------------------


def test_extract_monitor_name_from_up_title():
    assert uptime_service._extract_monitor_name_from_title("[UP] My Website") == "My Website"


def test_extract_monitor_name_from_down_title():
    assert uptime_service._extract_monitor_name_from_title("[DOWN] My Website") == "My Website"


def test_extract_monitor_name_from_title_no_prefix():
    assert uptime_service._extract_monitor_name_from_title("My Website") == "My Website"


def test_extract_monitor_name_strips_whitespace():
    assert uptime_service._extract_monitor_name_from_title("  [UP]   API Gateway  ") == "API Gateway"


def test_resolve_monitor_name_prefers_explicit_field():
    payload = UptimeKumaAlertPayload(
        status="up",
        monitor_name="Explicit Name",
        title="[UP] Title Name",
    )
    assert uptime_service._resolve_monitor_name(payload) == "Explicit Name"


def test_resolve_monitor_name_falls_back_to_title():
    payload = UptimeKumaAlertPayload(status="up", title="[DOWN] Title Name")
    assert uptime_service._resolve_monitor_name(payload) == "Title Name"


def test_resolve_monitor_name_returns_none_when_both_absent():
    payload = UptimeKumaAlertPayload(status="up")
    assert uptime_service._resolve_monitor_name(payload) is None


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def test_map_status_up_to_operational():
    assert uptime_service._map_alert_status_to_service_status("up", None) == "operational"


def test_map_status_down_to_outage():
    assert uptime_service._map_alert_status_to_service_status("down", None) == "outage"


def test_map_status_pending_to_degraded():
    assert uptime_service._map_alert_status_to_service_status("pending", None) == "degraded"


def test_map_status_maintenance():
    assert uptime_service._map_alert_status_to_service_status("maintenance", None) == "maintenance"


def test_map_apprise_type_success():
    assert uptime_service._map_alert_status_to_service_status("success", None) == "operational"


def test_map_apprise_type_failure():
    assert uptime_service._map_alert_status_to_service_status("failure", None) == "outage"


def test_map_apprise_type_warning():
    assert uptime_service._map_alert_status_to_service_status("warning", None) == "degraded"


def test_map_apprise_type_falls_back_to_alert_type():
    assert uptime_service._map_alert_status_to_service_status("unknown", "failure") == "outage"


def test_map_status_unknown_returns_none():
    assert uptime_service._map_alert_status_to_service_status("unknown_status", None) is None


# ---------------------------------------------------------------------------
# Service status sync
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_service_status_on_match(monkeypatch):
    """When a matching service exists, its status should be updated."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 5, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "my api":
            return {"id": 10, "name": "My API", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    payload = UptimeKumaAlertPayload(status="down", monitor_name="My API")
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert len(update_calls) == 1
    assert update_calls[0]["service_id"] == 10
    assert update_calls[0]["status"] == "outage"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_skips_sync_when_disabled(monkeypatch):
    """When sync_service_status is False no service lookup should occur."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    find_calls: list[str] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": False},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 6, **kwargs}

    async def fake_find_service_by_name(name: str):
        find_calls.append(name)
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    payload = UptimeKumaAlertPayload(status="down", monitor_name="My API")
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is False
    assert find_calls == []


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_from_apprise_title(monkeypatch):
    """Monitor name extracted from Apprise title should be used for matching."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 7, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "website":
            return {"id": 20, "name": "Website", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    # Apprise-format payload: no monitorName, uses title + type
    payload = UptimeKumaAlertPayload(
        status="failure",
        title="[DOWN] Website",
        alert_type="failure",
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert update_calls[0]["status"] == "outage"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_no_match_does_not_update(monkeypatch):
    """When no service matches the monitor name, no update should be made."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 8, **kwargs}

    async def fake_find_service_by_name(name: str):
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    payload = UptimeKumaAlertPayload(status="down", monitor_name="Unknown Monitor")
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is False


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_apprise_body_captured_as_message(monkeypatch):
    """Apprise body field should be stored as the alert message."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    captured: dict = {}

    async def fake_get_module(slug, *, redact=True):
        return {"enabled": True, "settings": {"shared_secret_hash": secret_hash}}

    async def fake_create_alert(**kwargs):
        captured.update(kwargs)
        return {"id": 9, **kwargs}

    async def fake_find_service_by_name(name):
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    payload = UptimeKumaAlertPayload.model_validate(
        {"status": "failure", "body": "Service is unreachable", "title": "[DOWN] My Service"}
    )
    await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert captured["message"] == "Service is unreachable"


# ---------------------------------------------------------------------------
# Apprise format parsing helpers
# ---------------------------------------------------------------------------


def test_extract_monitor_name_from_up_title():
    assert uptime_service._extract_monitor_name_from_title("[UP] My Website") == "My Website"


def test_extract_monitor_name_from_down_title():
    assert uptime_service._extract_monitor_name_from_title("[DOWN] My Website") == "My Website"


def test_extract_monitor_name_from_title_no_prefix():
    assert uptime_service._extract_monitor_name_from_title("My Website") == "My Website"


def test_extract_monitor_name_strips_whitespace():
    assert uptime_service._extract_monitor_name_from_title("  [UP]   API Gateway  ") == "API Gateway"


def test_resolve_monitor_name_prefers_explicit_field():
    payload = UptimeKumaAlertPayload(
        status="up",
        monitor_name="Explicit Name",
        title="[UP] Title Name",
    )
    assert uptime_service._resolve_monitor_name(payload) == "Explicit Name"


def test_resolve_monitor_name_falls_back_to_title():
    payload = UptimeKumaAlertPayload(status="up", title="[DOWN] Title Name")
    assert uptime_service._resolve_monitor_name(payload) == "Title Name"


def test_resolve_monitor_name_returns_none_when_both_absent():
    payload = UptimeKumaAlertPayload(status="up")
    assert uptime_service._resolve_monitor_name(payload) is None


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def test_map_status_up_to_operational():
    assert uptime_service._map_alert_status_to_service_status("up", None) == "operational"


def test_map_status_down_to_outage():
    assert uptime_service._map_alert_status_to_service_status("down", None) == "outage"


def test_map_status_pending_to_degraded():
    assert uptime_service._map_alert_status_to_service_status("pending", None) == "degraded"


def test_map_status_maintenance():
    assert uptime_service._map_alert_status_to_service_status("maintenance", None) == "maintenance"


def test_map_apprise_type_success():
    assert uptime_service._map_alert_status_to_service_status("success", None) == "operational"


def test_map_apprise_type_failure():
    assert uptime_service._map_alert_status_to_service_status("failure", None) == "outage"


def test_map_apprise_type_warning():
    assert uptime_service._map_alert_status_to_service_status("warning", None) == "degraded"


def test_map_apprise_type_falls_back_to_alert_type():
    assert uptime_service._map_alert_status_to_service_status("unknown", "failure") == "outage"


def test_map_status_unknown_returns_none():
    assert uptime_service._map_alert_status_to_service_status("unknown_status", None) is None


# ---------------------------------------------------------------------------
# Service status sync
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_service_status_on_match(monkeypatch):
    """When a matching service exists, its status should be updated."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 5, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "my api":
            return {"id": 10, "name": "My API", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    payload = UptimeKumaAlertPayload(status="down", monitor_name="My API")
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert len(update_calls) == 1
    assert update_calls[0]["service_id"] == 10
    assert update_calls[0]["status"] == "outage"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_skips_sync_when_disabled(monkeypatch):
    """When sync_service_status is False no service lookup should occur."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    find_calls: list[str] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": False},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 6, **kwargs}

    async def fake_find_service_by_name(name: str):
        find_calls.append(name)
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    payload = UptimeKumaAlertPayload(status="down", monitor_name="My API")
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is False
    assert find_calls == []


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_from_apprise_title(monkeypatch):
    """Monitor name extracted from Apprise title should be used for matching."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 7, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "website":
            return {"id": 20, "name": "Website", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    # Apprise-format payload: no monitorName, uses title + type
    payload = UptimeKumaAlertPayload(
        status="failure",
        title="[DOWN] Website",
        alert_type="failure",
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert update_calls[0]["status"] == "outage"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_no_match_does_not_update(monkeypatch):
    """When no service matches the monitor name, no update should be made."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 8, **kwargs}

    async def fake_find_service_by_name(name: str):
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    payload = UptimeKumaAlertPayload(status="down", monitor_name="Unknown Monitor")
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is False


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_apprise_body_captured_as_message(monkeypatch):
    """Apprise body field should be stored as the alert message."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    captured: dict = {}

    async def fake_get_module(slug, *, redact=True):
        return {"enabled": True, "settings": {"shared_secret_hash": secret_hash}}

    async def fake_create_alert(**kwargs):
        captured.update(kwargs)
        return {"id": 9, **kwargs}

    async def fake_find_service_by_name(name):
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    payload = UptimeKumaAlertPayload.model_validate(
        {"status": "failure", "body": "Service is unreachable", "title": "[DOWN] My Service"}
    )
    await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert captured["message"] == "Service is unreachable"
