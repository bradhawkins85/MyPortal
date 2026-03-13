import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.services import uptimekuma as uptime_service
from app.services import webhook_monitor


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def test_receive_alert_returns_accepted(monkeypatch):
    logged_calls = []

    async def fake_ingest_alert(**kwargs):
        assert kwargs["provided_secret"] == "secret-token"
        return {"id": 77, "monitor_name": "My Server"}

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "up"},
            headers={"Authorization": "Bearer secret-token"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["alert_id"] == 77

    assert len(logged_calls) == 1
    log = logged_calls[0]
    assert log["name"] == "Uptime Kuma Webhook - My Server"
    assert log["response_status"] == 202
    assert log.get("error_message") is None


def test_receive_alert_unauthorised(monkeypatch):
    logged_calls = []

    async def fake_ingest_alert(**_kwargs):
        raise uptime_service.AuthenticationError("Invalid token")

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "down"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"

    assert len(logged_calls) == 1
    log = logged_calls[0]
    assert "Authentication Failed" in log["name"]
    assert log["response_status"] == 401
    assert log["error_message"] == "Invalid token"


def test_receive_alert_module_disabled(monkeypatch):
    logged_calls = []

    async def fake_ingest_alert(**_kwargs):
        raise uptime_service.ModuleDisabledError("Module is disabled")

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "down"},
        )

    assert response.status_code == 503

    assert len(logged_calls) == 1
    log = logged_calls[0]
    assert "Module Disabled" in log["name"]
    assert log["response_status"] == 503
    assert log["error_message"] == "Module is disabled"


def test_receive_alert_invalid_payload_logs_error(monkeypatch):
    logged_calls = []

    async def fake_ingest_alert(**_kwargs):
        raise ValueError("Bad data")

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "down"},
        )

    assert response.status_code == 400

    assert len(logged_calls) == 1
    log = logged_calls[0]
    assert "Invalid Payload" in log["name"]
    assert log["response_status"] == 400
    assert log["error_message"] == "Bad data"


def test_list_alerts_returns_records(monkeypatch):
    async def fake_list_alerts(**kwargs):
        assert kwargs["status"] == "down"
        return [
            {
                "id": 1,
                "status": "down",
                "importance": True,
                "monitor_name": "API",
                "payload": {"status": "down"},
                "received_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
                "event_uuid": "abc",
            }
        ]

    monkeypatch.setattr(uptime_service, "list_alerts", fake_list_alerts)

    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/integration-modules/uptimekuma/alerts",
                params={"status": "down"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data[0]["status"] == "down"
    assert data[0]["importance"] is True


def test_get_alert_not_found(monkeypatch):
    async def fake_get_alert(alert_id: int):
        assert alert_id == 99
        return None

    monkeypatch.setattr(uptime_service, "get_alert", fake_get_alert)

    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/integration-modules/uptimekuma/alerts/99")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Alert not found"

def test_receive_alert_unexpected_error_logs_webhook(monkeypatch):
    """Unexpected exceptions from ingest_alert must still be logged as failed webhooks."""
    logged_calls = []

    async def fake_ingest_alert(**_kwargs):
        raise RuntimeError("Unexpected DB failure")

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "down"},
        )

    assert response.status_code == 500

    assert len(logged_calls) == 1
    log = logged_calls[0]
    assert "Error" in log["name"]
    assert log["response_status"] == 500
    assert log["error_message"] == "Unexpected DB failure"


def test_receive_alert_apprise_payload_accepted(monkeypatch):
    """Apprise-format payloads (no 'status' field, only 'type') must return 202."""
    logged_calls = []

    async def fake_ingest_alert(**kwargs):
        return {"id": 42, "monitor_name": "My API"}

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={
                "version": "1.0",
                "title": "[DOWN] My API",
                "message": "My API is unreachable",
                "type": "failure",
            },
            headers={"Authorization": "Bearer secret-token"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["alert_id"] == 42

    assert len(logged_calls) == 1
    assert logged_calls[0]["response_status"] == 202


def test_receive_alert_apprise_form_encoded_payload_accepted(monkeypatch):
    """Apprise can deliver as form data; endpoint should parse and accept it."""
    logged_calls = []

    async def fake_ingest_alert(**kwargs):
        payload = kwargs["payload"]
        assert payload.alert_type == "failure"
        assert payload.title == "[DOWN] Branch Firewall"
        return {"id": 88, "monitor_name": "Branch Firewall"}

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            data={
                "title": "[DOWN] Branch Firewall",
                "message": "Branch firewall is down",
                "type": "failure",
            },
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["alert_id"] == 88
    assert len(logged_calls) == 1
    assert logged_calls[0]["response_status"] == 202


def test_receive_alert_invalid_json_logs_webhook(monkeypatch):
    logged_calls = []

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            data='{"status":',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert len(logged_calls) == 1
    assert "Invalid JSON" in logged_calls[0]["name"]
    assert logged_calls[0]["response_status"] == 400


def test_receive_alert_csrf_exempt_with_query_token(monkeypatch):
    """Webhook must not be blocked by CSRF middleware when token is sent as a query param
    (i.e. no Authorization: Bearer header that would auto-bypass CSRF)."""

    async def fake_ingest_alert(**kwargs):
        assert kwargs["provided_secret"] == "query-token"
        return {"id": 55, "monitor_name": "My Monitor"}

    async def fake_log_incoming_webhook(**kwargs):
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)
    monkeypatch.setattr(main_module.settings, "enable_csrf", True)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            params={"token": "query-token"},
            json={"status": "up"},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_receive_alert_payload_secret_redacted_in_logs_and_raw_payload(monkeypatch):
    logged_calls = []

    async def fake_ingest_alert(**kwargs):
        assert kwargs["provided_secret"] == "payload-secret"
        assert kwargs["raw_payload"]["shared_secret"] == "[REDACTED]"
        return {"id": 99, "monitor_name": "Body Auth Monitor"}

    async def fake_log_incoming_webhook(**kwargs):
        logged_calls.append(kwargs)
        return {}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", fake_log_incoming_webhook)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "up", "shared_secret": "payload-secret"},
        )

    assert response.status_code == 202
    assert len(logged_calls) == 1
    assert logged_calls[0]["payload"]["shared_secret"] == "[REDACTED]"
