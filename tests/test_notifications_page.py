from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, notifications_repo, scheduler_service
from app.repositories import notification_preferences as preferences_repo


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    class DummyCursor:
        async def execute(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

    class DummyConnection:
        def cursor(self, *args, **kwargs):
            return DummyCursor()

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[DummyConnection]:
        yield DummyConnection()

    async def fake_connect():
        db._pool = object()  # type: ignore[attr-defined]
        return None

    async def fake_disconnect():
        db._pool = None  # type: ignore[attr-defined]
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "acquire", fake_acquire)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def patched_dependencies(monkeypatch):
    async def fake_require_user(request):
        return {"id": 1, "email": "user@example.com"}, None

    async def fake_count_notifications(**kwargs):
        if kwargs.get("read_state") == "unread":
            return 1
        return 2

    async def fake_list_notifications(**kwargs):
        created = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        return [
            {
                "id": 42,
                "event_type": "Test",
                "message": "Notification preview",
                "metadata": {"order": "ABC123"},
                "created_at": created,
                "read_at": None,
                "user_id": kwargs.get("user_id"),
            }
        ]

    async def fake_list_event_types(**kwargs):
        return ["Test"]

    async def fake_build_base_context(request, user, *, extra=None):
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2025,
            "current_user": user,
            "available_companies": [],
            "active_company": None,
            "active_company_id": None,
            "active_membership": None,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 1,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    monkeypatch.setattr(notifications_repo, "count_notifications", fake_count_notifications)
    monkeypatch.setattr(notifications_repo, "list_notifications", fake_list_notifications)
    monkeypatch.setattr(notifications_repo, "list_event_types", fake_list_event_types)

    yield


def test_notifications_page_returns_html(patched_dependencies):
    with TestClient(app) as client:
        response = client.get("/notifications")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Notification feed" in response.text
    assert "Mark selected as read" in response.text


def test_notification_settings_page_returns_html(monkeypatch, patched_dependencies):
    async def fake_list_preferences(user_id):
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

    monkeypatch.setattr(preferences_repo, "list_preferences", fake_list_preferences)

    with TestClient(app) as client:
        response = client.get("/notifications/settings")

    assert response.status_code == 200
    assert "Delivery preferences" in response.text
    assert "custom.event" in response.text
    assert "Back to feed" in response.text
