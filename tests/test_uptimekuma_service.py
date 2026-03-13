import hashlib
from datetime import datetime, timezone

import pytest

from app.schemas.uptimekuma import UptimeKumaAlertPayload, UptimeKumaTag
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
async def test_ingest_alert_rejects_when_secret_not_configured(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "settings": {"shared_secret_hash": ""}}

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

# ---------------------------------------------------------------------------
# _resolve_status helper
# ---------------------------------------------------------------------------


def test_resolve_status_uses_explicit_status():
    payload = UptimeKumaAlertPayload(status="down", alert_type="success")
    assert uptime_service._resolve_status(payload) == "down"


def test_resolve_status_falls_back_to_alert_type():
    payload = UptimeKumaAlertPayload(alert_type="failure")
    assert uptime_service._resolve_status(payload) == "failure"


def test_resolve_status_defaults_to_unknown_when_both_absent():
    payload = UptimeKumaAlertPayload()
    assert uptime_service._resolve_status(payload) == "unknown"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_apprise_only_payload(monkeypatch):
    """Apprise payloads with no 'status' field should be accepted and stored
    using alert_type as the status value."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    captured: dict = {}

    async def fake_get_module(slug, *, redact=True):
        return {"enabled": True, "settings": {"shared_secret_hash": secret_hash}}

    async def fake_create_alert(**kwargs):
        captured.update(kwargs)
        return {"id": 100, **kwargs}

    async def fake_find_service_by_name(name):
        return None

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)

    # Pure Apprise payload - no status field, only type/title/message
    payload = UptimeKumaAlertPayload.model_validate(
        {
            "version": "1.0",
            "title": "[DOWN] My API",
            "message": "My API is unreachable",
            "type": "failure",
        }
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={"title": "[DOWN] My API", "message": "My API is unreachable", "type": "failure"},
        provided_secret="tok",
        remote_addr="192.0.2.1",
        user_agent="Apprise/1.0",
    )

    assert record["id"] == 100
    assert captured["status"] == "failure"
    assert captured["monitor_name"] == "My API"
    assert captured["message"] == "My API is unreachable"
    assert captured["alert_type"] == "failure"


# ---------------------------------------------------------------------------
# Message body parsing helpers
# ---------------------------------------------------------------------------


def test_parse_message_up_with_emoji():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[ESDS Website] [✅ Up] 200 - OK"
    )
    assert name == "ESDS Website"
    assert status == "up"


def test_parse_message_down_with_emoji():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[ESDS Website] [🔴 Down] Some Error Message"
    )
    assert name == "ESDS Website"
    assert status == "down"


def test_parse_message_up_no_emoji():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[My Service] [Up] 200 - OK"
    )
    assert name == "My Service"
    assert status == "up"


def test_parse_message_down_no_emoji():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[My Service] [Down] Connection refused"
    )
    assert name == "My Service"
    assert status == "down"


def test_parse_message_pending():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[Health Check] [Pending] Waiting for response"
    )
    assert name == "Health Check"
    assert status == "pending"


def test_parse_message_maintenance():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[API Gateway] [Maintenance] Scheduled window"
    )
    assert name == "API Gateway"
    assert status == "maintenance"


def test_parse_message_case_insensitive():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[My Service] [DOWN] Error"
    )
    assert name == "My Service"
    assert status == "down"


def test_parse_message_no_match_returns_none_pair():
    name, status = uptime_service._parse_message_for_name_and_status(
        "Plain message without brackets"
    )
    assert name is None
    assert status is None


def test_parse_message_missing_status_bracket_returns_none():
    name, status = uptime_service._parse_message_for_name_and_status(
        "[My Service] No status bracket here"
    )
    assert name is None
    assert status is None


def test_resolve_monitor_name_falls_back_to_message():
    payload = UptimeKumaAlertPayload(
        message="[ESDS Website] [✅ Up] 200 - OK"
    )
    assert uptime_service._resolve_monitor_name(payload) == "ESDS Website"


def test_resolve_monitor_name_prefers_title_over_message():
    payload = UptimeKumaAlertPayload(
        title="[DOWN] Title Service",
        message="[Message Service] [🔴 Down] Error",
    )
    assert uptime_service._resolve_monitor_name(payload) == "Title Service"


def test_resolve_monitor_name_prefers_explicit_over_message():
    payload = UptimeKumaAlertPayload(
        monitor_name="Explicit Service",
        message="[Message Service] [🔴 Down] Error",
    )
    assert uptime_service._resolve_monitor_name(payload) == "Explicit Service"


def test_resolve_status_falls_back_to_message_up():
    payload = UptimeKumaAlertPayload(
        message="[ESDS Website] [✅ Up] 200 - OK"
    )
    assert uptime_service._resolve_status(payload) == "up"


def test_resolve_status_falls_back_to_message_down():
    payload = UptimeKumaAlertPayload(
        message="[ESDS Website] [🔴 Down] Some Error Message"
    )
    assert uptime_service._resolve_status(payload) == "down"


def test_resolve_status_prefers_explicit_over_message():
    payload = UptimeKumaAlertPayload(
        status="up",
        message="[ESDS Website] [🔴 Down] Some Error",
    )
    assert uptime_service._resolve_status(payload) == "up"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_from_message_body(monkeypatch):
    """Service name and status extracted from the message body should be used
    when monitor_name, title and status fields are all absent."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 30, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "esds website":
            return {"id": 50, "name": "ESDS Website", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    payload = UptimeKumaAlertPayload.model_validate(
        {"message": "[ESDS Website] [🔴 Down] Some Error Message"}
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={"message": "[ESDS Website] [🔴 Down] Some Error Message"},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert len(update_calls) == 1
    assert update_calls[0]["service_id"] == 50
    assert update_calls[0]["status"] == "outage"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_up_from_message_body(monkeypatch):
    """Up status in message body should resolve to 'operational'."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 31, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "esds website":
            return {"id": 50, "name": "ESDS Website", "status": "outage"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    payload = UptimeKumaAlertPayload.model_validate(
        {"message": "[ESDS Website] [✅ Up] 200 - OK"}
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={"message": "[ESDS Website] [✅ Up] 200 - OK"},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert len(update_calls) == 1
    assert update_calls[0]["status"] == "operational"


# ---------------------------------------------------------------------------
# Tag schema - UptimeKumaTag
# ---------------------------------------------------------------------------


def test_uptimekuma_tag_parses_full_object():
    tag = UptimeKumaTag(tag_id=1, monitor_id=52, value="ESDS Website", name="MyPortal Service", color="#059669")
    assert tag.tag_id == 1
    assert tag.monitor_id == 52
    assert tag.value == "ESDS Website"
    assert tag.name == "MyPortal Service"
    assert tag.color == "#059669"


def test_uptimekuma_tag_allows_missing_fields():
    tag = UptimeKumaTag(name="MyPortal Service", value="Some Service")
    assert tag.tag_id is None
    assert tag.monitor_id is None


def test_payload_coerces_structured_tags():
    payload = UptimeKumaAlertPayload.model_validate(
        {
            "status": "up",
            "tags": [
                {"tag_id": 1, "monitor_id": 52, "value": "ESDS Website", "name": "MyPortal Service", "color": "#059669"}
            ],
        }
    )
    assert payload.tags is not None
    assert len(payload.tags) == 1
    assert payload.tags[0].name == "MyPortal Service"
    assert payload.tags[0].value == "ESDS Website"


def test_payload_coerces_plain_string_tags():
    payload = UptimeKumaAlertPayload.model_validate({"status": "up", "tags": ["production", "critical"]})
    assert payload.tags is not None
    assert len(payload.tags) == 2
    assert payload.tags[0].name == "production"
    assert payload.tags[1].name == "critical"


def test_payload_tags_none_when_empty_list():
    payload = UptimeKumaAlertPayload.model_validate({"status": "up", "tags": []})
    assert payload.tags is None


# ---------------------------------------------------------------------------
# _resolve_myportal_service_name_from_tags helper
# ---------------------------------------------------------------------------


def test_resolve_myportal_tag_returns_value():
    tags = [
        UptimeKumaTag(tag_id=1, monitor_id=52, value="ESDS Website", name="MyPortal Service", color="#059669")
    ]
    assert uptime_service._resolve_myportal_service_name_from_tags(tags) == "ESDS Website"


def test_resolve_myportal_tag_case_insensitive_name():
    tags = [UptimeKumaTag(name="myportal service", value="My Service")]
    assert uptime_service._resolve_myportal_service_name_from_tags(tags) == "My Service"


def test_resolve_myportal_tag_ignores_other_tags():
    tags = [
        UptimeKumaTag(name="environment", value="production"),
        UptimeKumaTag(name="MyPortal Service", value="API Gateway"),
    ]
    assert uptime_service._resolve_myportal_service_name_from_tags(tags) == "API Gateway"


def test_resolve_myportal_tag_returns_none_when_no_matching_tag():
    tags = [UptimeKumaTag(name="environment", value="production")]
    assert uptime_service._resolve_myportal_service_name_from_tags(tags) is None


def test_resolve_myportal_tag_returns_none_when_tags_is_none():
    assert uptime_service._resolve_myportal_service_name_from_tags(None) is None


def test_resolve_myportal_tag_returns_none_when_value_is_empty():
    tags = [UptimeKumaTag(name="MyPortal Service", value="")]
    assert uptime_service._resolve_myportal_service_name_from_tags(tags) is None


def test_resolve_myportal_tag_strips_whitespace_from_value():
    tags = [UptimeKumaTag(name="MyPortal Service", value="  ESDS Website  ")]
    assert uptime_service._resolve_myportal_service_name_from_tags(tags) == "ESDS Website"


# ---------------------------------------------------------------------------
# Service sync using tags (integration-style with monkeypatching)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_syncs_from_myportal_service_tag(monkeypatch):
    """When the payload contains a 'MyPortal Service' tag, its value should
    be used to look up and update the corresponding service."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 40, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "esds website":
            return {"id": 70, "name": "ESDS Website", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    payload = UptimeKumaAlertPayload.model_validate(
        {
            "status": "down",
            "monitorName": "monitor-52",
            "tags": [
                {
                    "tag_id": 1,
                    "monitor_id": 52,
                    "value": "ESDS Website",
                    "name": "MyPortal Service",
                    "color": "#059669",
                }
            ],
        }
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert len(update_calls) == 1
    assert update_calls[0]["service_id"] == 70
    assert update_calls[0]["status"] == "outage"


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_tag_takes_priority_over_monitor_name(monkeypatch):
    """The 'MyPortal Service' tag value should be preferred over the monitor
    name when looking up the service to update."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    lookup_names: list[str] = []
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 41, **kwargs}

    async def fake_find_service_by_name(name: str):
        lookup_names.append(name)
        if name.lower() == "portal service name":
            return {"id": 80, "name": "Portal Service Name", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    payload = UptimeKumaAlertPayload.model_validate(
        {
            "status": "up",
            # monitor_name differs from the tag value
            "monitorName": "kuma-monitor-name",
            "tags": [
                {"tag_id": 2, "monitor_id": 10, "value": "Portal Service Name", "name": "MyPortal Service"}
            ],
        }
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    # The service lookup should have used the tag value, not the monitor name
    assert "portal service name" in [n.lower() for n in lookup_names]
    assert "kuma-monitor-name" not in [n.lower() for n in lookup_names]
    assert record["service_status_updated"] is True
    assert update_calls[0]["service_id"] == 80


@pytest.mark.anyio("asyncio")
async def test_ingest_alert_falls_back_to_monitor_name_when_no_tag(monkeypatch):
    """When no 'MyPortal Service' tag is present, the monitor name should
    still be used for the service lookup."""
    secret_hash = hashlib.sha256("tok".encode()).hexdigest()
    update_calls: list[dict] = []

    async def fake_get_module(slug, *, redact=True):
        return {
            "enabled": True,
            "settings": {"shared_secret_hash": secret_hash, "sync_service_status": True},
        }

    async def fake_create_alert(**kwargs):
        return {"id": 42, **kwargs}

    async def fake_find_service_by_name(name: str):
        if name.lower() == "my api":
            return {"id": 90, "name": "My API", "status": "operational"}
        return None

    async def fake_update_service(service_id, updates, *, company_ids=None):
        update_calls.append({"service_id": service_id, **updates})
        return {}

    monkeypatch.setattr(uptime_service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(uptime_service.alerts_repo, "create_alert", fake_create_alert)
    monkeypatch.setattr(uptime_service.service_status_repo, "find_service_by_name", fake_find_service_by_name)
    monkeypatch.setattr(uptime_service.service_status_repo, "update_service", fake_update_service)

    # Tags present but no "MyPortal Service" tag
    payload = UptimeKumaAlertPayload.model_validate(
        {
            "status": "down",
            "monitorName": "My API",
            "tags": [{"tag_id": 3, "monitor_id": 5, "value": "prod", "name": "environment"}],
        }
    )
    record = await uptime_service.ingest_alert(
        payload=payload,
        raw_payload={},
        provided_secret="tok",
        remote_addr=None,
        user_agent=None,
    )

    assert record["service_status_updated"] is True
    assert update_calls[0]["service_id"] == 90
    assert update_calls[0]["status"] == "outage"
