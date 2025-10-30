from datetime import date, datetime, timedelta, timezone

import app.main as main_module
import pytest
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import api_keys as api_key_repo
from app.services import audit as audit_service
from app.security.session import SessionData, session_manager


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

    async def fake_sync_change_log_sources(*_, **__):
        return None

    async def fake_ensure_modules(*_, **__):
        return None

    async def fake_refresh_schedules(*_, **__):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_sync_change_log_sources)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", fake_refresh_schedules)

    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
        session_token="session-token",
        csrf_token="test-csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=None,
    )

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)


def _make_existing_key():
    return {
        "id": 5,
        "description": "Legacy integration",
        "expiry_date": date(2025, 1, 1),
        "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        "last_used_at": datetime(2024, 12, 30, 8, 0, tzinfo=timezone.utc),
        "last_seen_at": datetime(2024, 12, 30, 8, 0, tzinfo=timezone.utc),
        "usage_count": 10,
        "key_prefix": "abcd1234",
        "usage": [],
        "permissions": [{"path": "/api/orders", "methods": ["GET"]}],
    }


def _make_updated_key():
    return {
        "id": 5,
        "description": "Updated integration",
        "expiry_date": date(2026, 2, 15),
        "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        "last_used_at": datetime(2024, 12, 30, 8, 0, tzinfo=timezone.utc),
        "last_seen_at": datetime(2024, 12, 30, 8, 0, tzinfo=timezone.utc),
        "usage_count": 10,
        "key_prefix": "abcd1234",
        "usage": [],
        "permissions": [{"path": "/api/orders", "methods": ["GET", "POST"]}],
    }


def test_api_update_key_saves_changes(monkeypatch):
    existing = _make_existing_key()
    updated = _make_updated_key()
    update_calls: dict[str, tuple] = {}
    audit_calls: list[dict] = []

    async def fake_get(api_key_id: int):
        assert api_key_id == 5
        return existing

    async def fake_update(api_key_id: int, *, description, expiry_date, permissions):
        update_calls["args"] = (api_key_id, description, expiry_date, permissions)
        return updated

    async def fake_log_action(**payload):  # pragma: no cover - assertion via stored state
        audit_calls.append(payload)

    monkeypatch.setattr(api_key_repo, "get_api_key_with_usage", fake_get)
    monkeypatch.setattr(api_key_repo, "update_api_key", fake_update)
    monkeypatch.setattr(audit_service, "log_action", fake_log_action)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 7,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api-keys/5",
                json={
                    "description": " Updated integration  ",
                    "expiry_date": "2026-02-15",
                    "permissions": [
                        {"path": "/api/orders", "methods": ["GET", "POST"]},
                    ],
                },
                headers={"X-CSRF-Token": "test-csrf-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "Updated integration"
    assert body["permissions"] == [{"path": "/api/orders", "methods": ["GET", "POST"]}]
    assert update_calls["args"] == (
        5,
        "Updated integration",
        date(2026, 2, 15),
        [
            {"path": "/api/orders", "methods": ["GET", "POST"]},
        ],
    )
    assert audit_calls
    audit_payload = audit_calls[0]
    assert audit_payload["action"] == "api_keys.update"
    assert audit_payload["entity_id"] == 5


def test_api_update_key_returns_404(monkeypatch):
    async def fake_get(_: int):
        return None

    monkeypatch.setattr(api_key_repo, "get_api_key_with_usage", fake_get)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api-keys/999",
                json={"description": "Does not matter"},
                headers={"X-CSRF-Token": "test-csrf-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404, response.json()


def test_admin_update_key_renders_success(monkeypatch):
    existing = _make_existing_key()
    updated = _make_updated_key()
    render_calls: list[dict] = []

    async def fake_require_super_admin_page(request):
        return {"id": 2, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_get(api_key_id: int):
        assert api_key_id == 5
        return existing

    async def fake_update(api_key_id: int, *, description, expiry_date, permissions):
        assert description == "Updated integration"
        assert expiry_date == date(2026, 2, 15)
        assert permissions == [{"path": "/api/orders", "methods": ["GET", "POST"]}]
        return updated

    async def fake_log_action(**_payload):
        return None

    async def fake_render(request, current_user, **kwargs):
        render_calls.append({"user": current_user, "kwargs": kwargs})
        return HTMLResponse("ok")

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(api_key_repo, "get_api_key_with_usage", fake_get)
    monkeypatch.setattr(api_key_repo, "update_api_key", fake_update)
    monkeypatch.setattr(audit_service, "log_action", fake_log_action)
    monkeypatch.setattr(main_module, "_render_api_keys_dashboard", fake_render)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/api-keys/update",
                data={
                    "api_key_id": "5",
                    "description": "Updated integration",
                    "expiry_date": "2026-02-15",
                    "permissions": "GET, POST /api/orders",
                    "_csrf": "test-csrf-token",
                },
                headers={"X-CSRF-Token": "test-csrf-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert render_calls
    render_kwargs = render_calls[0]["kwargs"]
    assert render_kwargs["status_message"] == "API key changes saved."
    assert render_kwargs["errors"] is None
