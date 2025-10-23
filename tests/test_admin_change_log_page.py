from __future__ import annotations

from datetime import datetime, timezone

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


def test_change_log_page_renders_entries(super_admin_context, monkeypatch):
    async def fake_list_change_types():
        return ["Feature", "Fix"]

    async def fake_list_entries(*, change_type=None, limit):
        assert change_type is None
        assert limit == 100
        return [
            {
                "guid": "1",
                "occurred_at_utc": datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
                "change_type": "Feature",
                "summary": "Added change log page",
                "source_file": "changes/example.json",
            }
        ]

    monkeypatch.setattr(main_module.change_log_repo, "list_change_types", fake_list_change_types)
    monkeypatch.setattr(main_module.change_log_repo, "list_change_log_entries", fake_list_entries)

    with TestClient(app) as client:
        response = client.get("/admin/change-log")

    assert response.status_code == 200
    html = response.text
    assert "Added change log page" in html
    assert "changes/example.json" in html
    assert "data-utc=\"2025-01-01T12:30:00+00:00\"" in html


def test_change_log_page_filters_type(super_admin_context, monkeypatch):
    async def fake_list_change_types():
        return ["Feature", "Fix"]

    recorded_args: dict[str, tuple[str | None, int]] = {}

    async def fake_list_entries(*, change_type=None, limit):
        recorded_args["call"] = (change_type, limit)
        return []

    monkeypatch.setattr(main_module.change_log_repo, "list_change_types", fake_list_change_types)
    monkeypatch.setattr(main_module.change_log_repo, "list_change_log_entries", fake_list_entries)

    with TestClient(app) as client:
        response = client.get("/admin/change-log", params={"change_type": "fix", "limit": 25})

    assert response.status_code == 200
    assert recorded_args["call"] == ("Fix", 25)
    assert "value=\"Fix\"\n                selected" in response.text
