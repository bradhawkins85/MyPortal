from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import app.main as main_module
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData


async def _dummy_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


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


@pytest.fixture
def csrf_session(monkeypatch):
    """Fixture that provides a session with a known CSRF token for POST tests."""
    now = datetime.now(timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
        session_token="test-token",
        csrf_token="test-csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=None,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, allow_inactive=False):
        return session

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    return session


def _make_post_request(path: str):
    scope = {"type": "http", "method": "POST", "path": path, "headers": []}
    return main_module.Request(scope, _dummy_receive)


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
    monkeypatch.setattr(
        main_module.system_state_service,
        "get_upgrade_status",
        lambda: {
            "configured_mode": "graceful",
            "pending": True,
            "requested_mode": "rolling",
            "requested_reason": "deployment_topology_changed",
            "last_status": "succeeded",
            "last_mode": "graceful",
        },
    )

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
    assert "Upgrade mode:" in html
    assert "Pending upgrade:" in html
    assert "rolling" in html
    _, separator, header_html = html.partition('class="header__actions" data-header-actions')
    assert separator
    header_html, _, _ = header_html.partition("</header>")
    assert 'aria-controls="scheduled-tasks-bulk-actions-menu"' in header_html
    assert 'id="scheduled-tasks-bulk-actions-menu"' in header_html
    assert ">Manage Tasks<" not in header_html
    assert 'data-task-create' in header_html
    assert 'Redistribute selected' in header_html
    assert 'data-scheduled-tasks-bulk-action="redistribute"' in header_html
    assert 'data-scheduled-tasks-redistribute-offset' in html


def test_scheduled_tasks_page_offers_rag_control_commands(super_admin_context, monkeypatch):
    """The create/edit modal exposes optional RAG maintenance commands even when no task exists yet."""

    async def fake_list_tasks(include_inactive=False):
        return []

    async def fake_list_companies():
        return []

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)

    with TestClient(app) as client:
        response = client.get("/admin/scheduled-tasks")

    assert response.status_code == 200
    html = response.text
    for value, label in [
        ("rag_index_start", "RAG start indexing"),
        ("rag_index_stop", "RAG stop indexing"),
        ("rag_matching_pause", "RAG pause matching"),
        ("rag_matching_resume", "RAG resume matching"),
        ("rag_cleanup_stale_matches", "RAG cleanup stale matches"),
    ]:
        assert f'<option value="{value}">{label}</option>' in html


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


def test_scheduled_tasks_page_edit_button_and_modal_present(super_admin_context, monkeypatch):
    """Test that the Edit button and task editor modal are rendered on the page."""

    async def fake_list_tasks(include_inactive=False):
        return [
            {
                "id": 1,
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
    assert 'data-task-edit' in html
    assert 'id="task-editor-modal"' in html
    assert 'id="scheduled-task-form"' in html
    assert 'id="task-command" name="command" required data-initial-focus' in html
    assert 'id="task-name-display"' in html


def test_admin_automation_redirects(super_admin_context):
    """GET /admin/automation should permanently redirect to /admin/scheduled-tasks."""
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/admin/automation")

    assert response.status_code == 301
    assert response.headers["location"] == "/admin/scheduled-tasks"


def test_bulk_delete_scheduled_tasks(super_admin_context, csrf_session, monkeypatch):
    """Test that bulk delete removes the selected tasks and redirects with a success message."""
    deleted: list[list[int]] = []

    async def fake_delete_tasks(task_ids):
        deleted.append(list(task_ids))
        return len(task_ids)

    async def fake_refresh():
        return None

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "delete_tasks", fake_delete_tasks)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", fake_refresh)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/admin/scheduled-tasks/bulk-delete",
            data={"taskIds": ["1", "2"], "_csrf": csrf_session.csrf_token},
        )

    assert response.status_code == 303
    assert "success=" not in response.headers["location"]
    assert "error=" not in response.headers["location"]
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "success" in flash_cookie
    assert deleted == [[1, 2]]


def test_bulk_delete_scheduled_tasks_no_ids(super_admin_context, csrf_session):
    """Bulk delete with no IDs redirects with an error message."""
    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/admin/scheduled-tasks/bulk-delete",
            data={"_csrf": csrf_session.csrf_token},
        )

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "error" in flash_cookie


@pytest.mark.asyncio
async def test_bulk_delete_scheduled_tasks_requires_super_admin(monkeypatch, csrf_session):
    """Bulk delete redirects non-super-admins."""
    from fastapi.responses import RedirectResponse

    async def fake_require_super_admin_page(request):
        return None, RedirectResponse(url="/login", status_code=302)

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    request = _make_post_request("/admin/scheduled-tasks/bulk-delete")

    async def fake_form():
        return FormData([("taskIds", "1"), ("_csrf", csrf_session.csrf_token)])

    monkeypatch.setattr(request, "form", fake_form)

    response = await main_module.admin_bulk_delete_scheduled_tasks(request)

    assert response.status_code == 302


@pytest.mark.asyncio
async def test_bulk_rename_scheduled_tasks(super_admin_context, csrf_session, monkeypatch):
    """Test that bulk rename updates task names to 'Company — Command' format."""
    renamed: dict[int, str] = {}

    async def fake_get_task(task_id):
        tasks = {
            1: {
                "id": 1,
                "name": "Old name",
                "command": "sync_staff",
                "company_id": None,
                "active": True,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "description": None,
                "max_retries": 12,
                "retry_backoff_seconds": 300,
            },
            2: {
                "id": 2,
                "name": "Another name",
                "command": "sync_m365_licenses",
                "company_id": 42,
                "active": True,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "description": None,
                "max_retries": 12,
                "retry_backoff_seconds": 300,
            },
        }
        return tasks.get(task_id)

    async def fake_rename_task(task_id, name):
        renamed[task_id] = name

    async def fake_list_companies():
        return [{"id": 42, "name": "Acme Corp"}]

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task", fake_get_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "rename_task", fake_rename_task)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(
        main_module.system_state_service,
        "get_upgrade_status",
        lambda: {"configured_mode": "graceful"},
    )
    request = _make_post_request("/admin/scheduled-tasks/bulk-rename")

    async def fake_form():
        return FormData([("taskIds", "1"), ("taskIds", "2"), ("_csrf", csrf_session.csrf_token)])

    monkeypatch.setattr(request, "form", fake_form)

    response = await main_module.admin_bulk_rename_scheduled_tasks(request)

    assert response.status_code == 303
    assert "success=" not in response.headers["location"]
    assert "error=" not in response.headers["location"]
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "success" in flash_cookie
    assert renamed[1] == "All companies \u2014 Sync staff directory"
    assert renamed[2] == "Acme Corp \u2014 Sync Microsoft 365 licenses"


@pytest.mark.asyncio
async def test_bulk_rename_unknown_command(super_admin_context, csrf_session, monkeypatch):
    """Bulk rename falls back to the raw command string for unknown commands."""
    renamed: dict[int, str] = {}

    async def fake_get_task(task_id):
        return {
            "id": task_id,
            "name": "Old name",
            "command": "custom_command",
            "company_id": None,
            "active": True,
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "description": None,
            "max_retries": 12,
            "retry_backoff_seconds": 300,
        }

    async def fake_rename_task(task_id, name):
        renamed[task_id] = name

    async def fake_list_companies():
        return []

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task", fake_get_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "rename_task", fake_rename_task)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)
    request = _make_post_request("/admin/scheduled-tasks/bulk-rename")

    async def fake_form():
        return FormData([("taskIds", "5"), ("_csrf", csrf_session.csrf_token)])

    monkeypatch.setattr(request, "form", fake_form)

    response = await main_module.admin_bulk_rename_scheduled_tasks(request)

    assert response.status_code == 303
    assert renamed[5] == "All companies \u2014 custom_command"


@pytest.mark.asyncio
async def test_bulk_rename_no_ids(super_admin_context, csrf_session, monkeypatch):
    """Bulk rename with no IDs redirects with an error message."""
    monkeypatch.setattr(
        main_module.system_state_service,
        "get_upgrade_status",
        lambda: {"configured_mode": "graceful"},
    )
    request = _make_post_request("/admin/scheduled-tasks/bulk-rename")

    async def fake_form():
        return FormData([("_csrf", csrf_session.csrf_token)])

    monkeypatch.setattr(request, "form", fake_form)
    response = await main_module.admin_bulk_rename_scheduled_tasks(request)

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "error" in flash_cookie


def test_bulk_redistribute_scheduled_tasks(super_admin_context, csrf_session, monkeypatch):
    """Bulk redistribute updates cron minutes one minute apart at the requested hour."""
    updated: dict[int, str] = {}
    refreshed = False

    async def fake_get_task(task_id):
        tasks = {
            1: {"id": 1, "cron": "15 4 * * *"},
            2: {"id": 2, "cron": "30 5 * * 1"},
            3: {"id": 3, "cron": "45 6 * * * extra"},
        }
        return tasks.get(task_id)

    async def fake_update_task_cron(task_id, cron):
        updated[task_id] = cron

    async def fake_refresh():
        nonlocal refreshed
        refreshed = True

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task", fake_get_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "update_task_cron", fake_update_task_cron)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", fake_refresh)
    request = _make_post_request("/admin/scheduled-tasks/bulk-redistribute")

    async def fake_form():
        return FormData(
            [
                ("taskIds", "1"),
                ("taskIds", "2"),
                ("taskIds", "3"),
                ("redistributeHour", "9"),
                ("_csrf", csrf_session.csrf_token),
            ]
        )

    monkeypatch.setattr(request, "form", fake_form)

    response = asyncio.run(main_module.admin_bulk_redistribute_scheduled_tasks(request))

    assert response.status_code == 303
    assert updated == {
        1: "0 9 * * *",
        2: "1 9 * * 1",
        3: "2 9 * * * extra",
    }
    assert refreshed is True


def test_bulk_redistribute_scheduled_tasks_rolls_into_later_hours(super_admin_context, csrf_session, monkeypatch):
    """Bulk redistribute keeps spacing tasks one minute apart beyond the first hour."""
    updated: dict[int, str] = {}
    refreshed = False

    async def fake_get_task(task_id):
        return {"id": task_id, "cron": "15 4 * * *"}

    async def fake_update_task_cron(task_id, cron):
        updated[task_id] = cron

    async def fake_refresh():
        nonlocal refreshed
        refreshed = True

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task", fake_get_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "update_task_cron", fake_update_task_cron)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", fake_refresh)
    request = _make_post_request("/admin/scheduled-tasks/bulk-redistribute")

    async def fake_form():
        entries = [("taskIds", str(task_id)) for task_id in range(1, 63)]
        entries.extend([("redistributeHour", "23"), ("_csrf", csrf_session.csrf_token)])
        return FormData(entries)

    monkeypatch.setattr(request, "form", fake_form)

    response = asyncio.run(main_module.admin_bulk_redistribute_scheduled_tasks(request))

    assert response.status_code == 303
    assert len(updated) == 62
    assert updated[1] == "0 23 * * *"
    assert updated[60] == "59 23 * * *"
    assert updated[61] == "0 0 * * *"
    assert updated[62] == "1 0 * * *"
    assert refreshed is True


def test_bulk_redistribute_scheduled_tasks_uses_minute_offset(super_admin_context, csrf_session, monkeypatch):
    """Bulk redistribute starts at the requested minute offset and rolls over hours."""
    updated: dict[int, str] = {}
    refreshed = False

    async def fake_get_task(task_id):
        return {"id": task_id, "cron": "15 4 * * *"}

    async def fake_update_task_cron(task_id, cron):
        updated[task_id] = cron

    async def fake_refresh():
        nonlocal refreshed
        refreshed = True

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task", fake_get_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "update_task_cron", fake_update_task_cron)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", fake_refresh)
    request = _make_post_request("/admin/scheduled-tasks/bulk-redistribute")

    async def fake_form():
        return FormData(
            [
                ("taskIds", "1"),
                ("taskIds", "2"),
                ("taskIds", "3"),
                ("redistributeHour", "9"),
                ("redistributeOffset", "58"),
                ("_csrf", csrf_session.csrf_token),
            ]
        )

    monkeypatch.setattr(request, "form", fake_form)

    response = asyncio.run(main_module.admin_bulk_redistribute_scheduled_tasks(request))

    assert response.status_code == 303
    assert updated == {
        1: "58 9 * * *",
        2: "59 9 * * *",
        3: "0 10 * * *",
    }
    assert refreshed is True


def test_bulk_redistribute_rejects_invalid_offset(super_admin_context, csrf_session, monkeypatch):
    """Bulk redistribute validates the requested minute offset before updating tasks."""
    update_mock = AsyncMock()
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "update_task_cron", update_mock)
    request = _make_post_request("/admin/scheduled-tasks/bulk-redistribute")

    async def fake_form():
        return FormData(
            [
                ("taskIds", "1"),
                ("redistributeHour", "9"),
                ("redistributeOffset", "60"),
                ("_csrf", csrf_session.csrf_token),
            ]
        )

    monkeypatch.setattr(request, "form", fake_form)

    response = asyncio.run(main_module.admin_bulk_redistribute_scheduled_tasks(request))

    assert response.status_code == 303
    update_mock.assert_not_called()
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "error" in flash_cookie


def test_bulk_redistribute_rejects_invalid_hour(super_admin_context, csrf_session, monkeypatch):
    """Bulk redistribute validates the requested hour before updating tasks."""
    update_mock = AsyncMock()
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "update_task_cron", update_mock)
    request = _make_post_request("/admin/scheduled-tasks/bulk-redistribute")

    async def fake_form():
        return FormData([("taskIds", "1"), ("redistributeHour", "24"), ("_csrf", csrf_session.csrf_token)])

    monkeypatch.setattr(request, "form", fake_form)

    response = asyncio.run(main_module.admin_bulk_redistribute_scheduled_tasks(request))

    assert response.status_code == 303
    update_mock.assert_not_called()
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "error" in flash_cookie
