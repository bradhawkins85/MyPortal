import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.services import uptimekuma as uptime_service


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
    async def fake_ingest_alert(**kwargs):
        assert kwargs["provided_secret"] == "secret-token"
        return {"id": 77}

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)

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


def test_receive_alert_unauthorised(monkeypatch):
    async def fake_ingest_alert(**_kwargs):
        raise uptime_service.AuthenticationError("Invalid token")

    monkeypatch.setattr(uptime_service, "ingest_alert", fake_ingest_alert)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/uptimekuma/alerts",
            json={"status": "down"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


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
