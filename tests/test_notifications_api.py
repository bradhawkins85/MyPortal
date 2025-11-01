from __future__ import annotations

from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.core.notifications import DEFAULT_NOTIFICATION_EVENTS
from app.main import app, notifications_repo, scheduler_service
from app.services import notification_event_settings as event_settings_service
from app.repositories import notification_preferences as preferences_repo
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

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def active_session(monkeypatch):
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=None,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    return session


def _make_notification_record(**overrides):
    base = {
        "id": 1,
        "user_id": 42,
        "event_type": "system",
        "message": "Test notification",
        "metadata": {"example": True},
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "read_at": None,
    }
    base.update(overrides)
    return base


def _build_event_setting(event_type: str) -> dict[str, object]:
    base = deepcopy(DEFAULT_NOTIFICATION_EVENTS.get(event_type, {}))
    return {
        "event_type": event_type,
        "display_name": base.get("display_name") or event_type,
        "description": base.get("description"),
        "message_template": base.get("message_template") or "{{ message }}",
        "is_user_visible": base.get("is_user_visible", True),
        "allow_channel_in_app": base.get("allow_channel_in_app", True),
        "allow_channel_email": base.get("allow_channel_email", False),
        "allow_channel_sms": base.get("allow_channel_sms", False),
        "default_channel_in_app": base.get("default_channel_in_app", True),
        "default_channel_email": base.get("default_channel_email", False),
        "default_channel_sms": base.get("default_channel_sms", False),
        "module_actions": base.get("module_actions") or [],
    }


_DEFAULT_EVENT_TYPES_FOR_TESTS = [
    "general",
    "shop.shipping_status_updated",
    "shop.stock_notification",
]


def test_create_notification_requires_super_admin(monkeypatch, active_session):
    async def fake_create_notification(**_kwargs):
        return _make_notification_record()

    monkeypatch.setattr(notifications_repo, "create_notification", fake_create_notification)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 7,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications",
                json={"event_type": "system", "message": "Denied"},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_create_notification_returns_created_record(monkeypatch, active_session):
    created = _make_notification_record(id=99, message="Created via API")

    async def fake_create_notification(**kwargs):
        assert kwargs["event_type"] == "system"
        assert kwargs["message"] == "Created via API"
        assert kwargs["user_id"] == 123
        assert kwargs["metadata"] == {"source": "api"}
        return created

    monkeypatch.setattr(notifications_repo, "create_notification", fake_create_notification)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications",
                json={
                    "event_type": "system",
                    "message": "Created via API",
                    "user_id": 123,
                    "metadata": {"source": "api"},
                },
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == created["id"]
    assert data["message"] == created["message"]
    assert data["user_id"] == created["user_id"]
    assert data["metadata"] == created["metadata"]


def test_list_notification_preferences_merges_defaults(monkeypatch, active_session):
    async def fake_list_preferences(user_id):
        assert user_id == active_session.user_id
        return []

    async def fake_list_event_types(user_id):
        assert user_id == active_session.user_id
        return ["custom.event"]

    async def fake_list_event_settings(include_hidden=True):
        return [_build_event_setting(event) for event in _DEFAULT_EVENT_TYPES_FOR_TESTS]

    async def fake_get_event_setting(event_type):
        return _build_event_setting(event_type)

    monkeypatch.setattr(preferences_repo, "list_preferences", fake_list_preferences)
    monkeypatch.setattr(notifications_repo, "list_event_types", fake_list_event_types)
    monkeypatch.setattr(event_settings_service, "list_event_settings", fake_list_event_settings)
    monkeypatch.setattr(event_settings_service, "get_event_setting", fake_get_event_setting)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/notifications/preferences")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    event_types = {item["event_type"] for item in data}
    assert "general" in event_types
    assert "custom.event" in event_types
    assert "shop.shipping_status_updated" in event_types
    assert "shop.stock_notification" in event_types
    general = next(item for item in data if item["event_type"] == "general")
    assert general["allow_channel_in_app"] is True
    assert general["display_name"]


def test_update_notification_preferences_persists_changes(monkeypatch, active_session):
    received = {}

    async def fake_upsert_preferences(user_id, preferences):
        received["user_id"] = user_id
        received["preferences"] = preferences
        return [
            {
                "event_type": "general",
                "channel_in_app": True,
                "channel_email": False,
                "channel_sms": False,
            },
            {
                "event_type": "custom.event",
                "channel_in_app": False,
                "channel_email": True,
                "channel_sms": False,
            },
        ]

    async def fake_list_event_types(user_id):
        return []

    async def fake_list_event_settings(include_hidden=True):
        return [_build_event_setting(event) for event in _DEFAULT_EVENT_TYPES_FOR_TESTS]

    async def fake_get_event_setting(event_type):
        return _build_event_setting(event_type)

    monkeypatch.setattr(preferences_repo, "upsert_preferences", fake_upsert_preferences)
    monkeypatch.setattr(notifications_repo, "list_event_types", fake_list_event_types)
    monkeypatch.setattr(event_settings_service, "list_event_settings", fake_list_event_settings)
    monkeypatch.setattr(event_settings_service, "get_event_setting", fake_get_event_setting)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    payload = {
        "preferences": [
            {
                "event_type": "custom.event",
                "channel_in_app": False,
                "channel_email": True,
                "channel_sms": False,
            }
        ]
    }

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/notifications/preferences",
                json=payload,
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert received["user_id"] == active_session.user_id
    assert received["preferences"] == [
        {
            "event_type": "custom.event",
            "channel_in_app": False,
            "channel_email": False,
            "channel_sms": False,
        }
    ]
    data = response.json()
    event_types = {item["event_type"] for item in data}
    assert "general" in event_types
    custom = next(item for item in data if item["event_type"] == "custom.event")
    assert custom["channel_in_app"] is False
    assert custom["channel_email"] is False
    assert "allow_channel_in_app" in custom


def test_mark_notification_read_denies_other_users(monkeypatch, active_session):
    calls = {"mark": False}

    async def fake_get_notification(notification_id):
        return _make_notification_record(id=notification_id, user_id=999)

    async def fake_mark_read(notification_id):
        calls["mark"] = True
        return {}

    monkeypatch.setattr(notifications_repo, "get_notification", fake_get_notification)
    monkeypatch.setattr(notifications_repo, "mark_read", fake_mark_read)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications/77/read",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert calls["mark"] is False


def test_mark_notification_read_returns_updated_record(monkeypatch, active_session):
    async def fake_get_notification(notification_id):
        return _make_notification_record(id=notification_id, user_id=active_session.user_id)

    updated = _make_notification_record(
        id=77,
        user_id=active_session.user_id,
        read_at=datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
    )

    async def fake_mark_read(notification_id):
        assert notification_id == 77
        return updated

    monkeypatch.setattr(notifications_repo, "get_notification", fake_get_notification)
    monkeypatch.setattr(notifications_repo, "mark_read", fake_mark_read)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications/77/read",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 77
    assert data["read_at"] is not None


def test_acknowledge_notifications_returns_unique_updates(monkeypatch, active_session):
    calls: dict[str, object] = {"checked": []}

    async def fake_get_notification(notification_id):
        calls["checked"].append(notification_id)
        return _make_notification_record(id=notification_id, user_id=active_session.user_id)

    async def fake_mark_read_bulk(notification_ids):
        calls["marked"] = list(notification_ids)
        return [
            _make_notification_record(
                id=identifier,
                user_id=active_session.user_id,
                read_at=datetime(2025, 1, 1, 12, 45, tzinfo=timezone.utc),
            )
            for identifier in notification_ids
        ]

    monkeypatch.setattr(notifications_repo, "get_notification", fake_get_notification)
    monkeypatch.setattr(notifications_repo, "mark_read_bulk", fake_mark_read_bulk)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    payload = {"notification_ids": [10, 10, 12]}

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications/acknowledge",
                json=payload,
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert calls["checked"] == [10, 12]
    assert calls["marked"] == [10, 12]


def test_notification_summary_returns_counts(monkeypatch, active_session):
    calls: list[dict] = []

    async def fake_count_notifications(**kwargs):
        calls.append(kwargs)
        if kwargs.get("read_state") == "unread" and kwargs.get("event_types"):
            return 4
        if kwargs.get("read_state") == "unread":
            return 7
        return 11

    monkeypatch.setattr(notifications_repo, "count_notifications", fake_count_notifications)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/notifications/summary",
                params=[("event_type", "system"), ("search", "invoice late ")],
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "total_count": 11,
        "filtered_unread_count": 4,
        "global_unread_count": 7,
    }
    assert calls[0]["read_state"] is None
    assert calls[0]["event_types"] == ["system"]


def test_event_types_endpoint_merges_sources(monkeypatch, active_session):
    async def fake_list_preferences(user_id):
        assert user_id == active_session.user_id
        return [
            {
                "event_type": "custom.event",
                "channel_in_app": True,
                "channel_email": False,
                "channel_sms": False,
            },
            {
                "event_type": "general",
                "channel_in_app": True,
                "channel_email": False,
                "channel_sms": False,
            },
        ]

    async def fake_list_event_types(user_id):
        assert user_id == active_session.user_id
        return ["db.recorded"]

    monkeypatch.setattr(preferences_repo, "list_preferences", fake_list_preferences)
    monkeypatch.setattr(notifications_repo, "list_event_types", fake_list_event_types)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/notifications/event-types")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert "custom.event" in data
    assert "db.recorded" in data
    assert "general" in data
    assert "billing.invoice_overdue" in data
    assert len(data) == len(set(data))
