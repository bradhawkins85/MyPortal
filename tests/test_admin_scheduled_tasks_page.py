from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import app.main as main_module
import pytest
from fastapi.testclient import TestClient

from app.core.database import db
from app.main import app, scheduler_service


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

    async def fake_sync_change_log_sources():
        return None

    async def fake_ensure_modules():
        return None

    async def fake_refresh_schedules():
        return None

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return None

    async def fake_list_companies_for_user(user_id):
        return []

    async def fake_get_user_company(user_id, company_id):
        return {}

    async def fake_list_modules():
        return []

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_sync_change_log_sources)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", fake_refresh_schedules)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_company_repo, "list_companies_for_user", fake_list_companies_for_user)
    monkeypatch.setattr(main_module.user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(main_module.modules_service, "list_modules", fake_list_modules)


@pytest.fixture
def super_admin_context(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    yield


def test_scheduled_tasks_page_renders_tasks(super_admin_context, monkeypatch):
    """Test that the scheduled tasks page renders task data including company links."""
    last_run = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)

    async def fake_list_tasks(include_inactive=False):
        return [
            {
                "id": 1,
                "name": "Sync M365 data",
                "command": "sync_m365_data",
                "cron": "0 2 * * *",
                "company_id": 42,
                "active": True,
                "last_run_at": last_run,
                "last_status": "succeeded",
                "last_error": None,
                "description": None,
                "max_retries": 12,
                "retry_backoff_seconds": 300,
            },
            {
                "id": 2,
                "name": "Sync staff",
                "command": "sync_staff",
                "cron": "30 3 * * *",
                "company_id": None,
                "active": True,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "description": None,
                "max_retries": 12,
                "retry_backoff_seconds": 300,
            },
        ]

    async def fake_list_companies():
        return [{"id": 42, "name": "Acme Corp"}]

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)

    with TestClient(app) as client:
        response = client.get("/admin/scheduled-tasks")

    assert response.status_code == 200
    html = response.text
    assert "Sync M365 data" in html
    assert "sync_m365_data" in html
    assert "Acme Corp" in html
    assert "/admin/companies/42/edit" in html
    assert "Sync staff" in html
    assert "All companies" in html
    assert "2025-06-01T10:00:00+00:00" in html


def test_scheduled_tasks_page_empty(super_admin_context, monkeypatch):
    """Test that the page shows an empty state when no tasks are configured."""

    async def fake_list_tasks(include_inactive=False):
        return []

    async def fake_list_companies():
        return []

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)

    with TestClient(app) as client:
        response = client.get("/admin/scheduled-tasks")

    assert response.status_code == 200
    assert "No active scheduled tasks" in response.text


def test_scheduled_tasks_page_show_inactive(super_admin_context, monkeypatch):
    """Test that show_inactive=1 passes through to the repository."""
    captured: dict[str, bool] = {}

    async def fake_list_tasks(include_inactive=False):
        captured["include_inactive"] = include_inactive
        return []

    async def fake_list_companies():
        return []

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)

    with TestClient(app) as client:
        response = client.get("/admin/scheduled-tasks", params={"show_inactive": "1"})

    assert response.status_code == 200
    assert captured.get("include_inactive") is True


def test_scheduled_tasks_page_requires_super_admin(monkeypatch):
    """Test that the page redirects non-super-admins."""
    from fastapi.responses import RedirectResponse

    async def fake_require_super_admin_page(request):
        return None, RedirectResponse(url="/login", status_code=302)

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)

    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/admin/scheduled-tasks")

    assert response.status_code == 302


def test_scheduled_tasks_page_run_now_button_present(super_admin_context, monkeypatch):
    """Test that the Run now button is rendered in the actions column for each task."""

    async def fake_list_tasks(include_inactive=False):
        return [
            {
                "id": 1,
                "name": "Sync M365 data",
                "command": "sync_m365_data",
                "cron": "0 2 * * *",
                "company_id": None,
                "active": True,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "description": None,
                "max_retries": 12,
                "retry_backoff_seconds": 300,
            }
        ]

    async def fake_list_companies():
        return []

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)

    with TestClient(app) as client:
        response = client.get("/admin/scheduled-tasks")

    assert response.status_code == 200
    assert 'data-task-run' in response.text
    assert 'Run now' in response.text


def test_scheduled_tasks_page_failed_task_status(super_admin_context, monkeypatch):
    """Test that a failed task status is shown in the rendered table."""

    async def fake_list_tasks(include_inactive=False):
        return [
            {
                "id": 3,
                "name": "Failing task",
                "command": "sync_assets",
                "cron": "0 5 * * *",
                "company_id": None,
                "active": True,
                "last_run_at": datetime(2025, 6, 2, 5, 0, tzinfo=timezone.utc),
                "last_status": "failed",
                "last_error": "Connection refused",
                "description": None,
                "max_retries": 3,
                "retry_backoff_seconds": 300,
            }
        ]

    async def fake_list_companies():
        return []

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)

    with TestClient(app) as client:
        response = client.get("/admin/scheduled-tasks")

    assert response.status_code == 200
    html = response.text
    assert "Failing task" in html
    assert "failed" in html
