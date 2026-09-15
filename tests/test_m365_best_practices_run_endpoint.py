from __future__ import annotations

import base64
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.services.m365_best_practices as bp_service
from app.core.database import db
from app.main import app, scheduler_service


def _decode_flash_cookie(response) -> dict[str, str]:
    cookie_header = response.headers.get("set-cookie", "")
    assert "_flash=" in cookie_header
    raw_cookie = cookie_header.split("_flash=", 1)[1].split(";", 1)[0]
    signed = base64.b64decode(raw_cookie.encode("utf-8")).decode("utf-8")
    payload = signed.rsplit("|", 1)[0]
    return json.loads(payload)


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(db, "connect", noop)
    monkeypatch.setattr(db, "disconnect", noop)
    monkeypatch.setattr(db, "run_migrations", noop)
    monkeypatch.setattr(scheduler_service, "start", noop)
    monkeypatch.setattr(scheduler_service, "stop", noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


@pytest.mark.asyncio
async def test_reset_enabled_results_to_unknown_only_targets_enabled_non_excluded(monkeypatch):
    monkeypatch.setattr(
        bp_service,
        "_BEST_PRACTICES",
        [
            {"id": "bp_one", "name": "Check one"},
            {"id": "bp_two", "name": "Check two"},
        ],
    )
    monkeypatch.setattr(
        bp_service,
        "get_enabled_check_ids",
        AsyncMock(return_value={"bp_one", "bp_two"}),
    )
    monkeypatch.setattr(
        bp_service.bp_repo,
        "get_company_exclusions",
        AsyncMock(return_value={"bp_two"}),
    )
    upsert_mock = AsyncMock()
    monkeypatch.setattr(bp_service.bp_repo, "upsert_result", upsert_mock)

    reset_count = await bp_service.reset_enabled_results_to_unknown(42)

    assert reset_count == 1
    upsert_mock.assert_awaited_once()
    call_kwargs = upsert_mock.await_args.kwargs
    assert call_kwargs["company_id"] == 42
    assert call_kwargs["check_id"] == "bp_one"
    assert call_kwargs["check_name"] == "Check one"
    assert call_kwargs["status"] == bp_service.STATUS_UNKNOWN
    assert call_kwargs["details"] == "Evaluation in progress."
    assert isinstance(call_kwargs["run_at"], datetime)


def test_run_best_practices_resets_to_unknown_before_queueing(monkeypatch):
    async def fake_context(request, super_admin_only=False):
        return {"id": 7, "is_super_admin": True}, None, None, 99, None

    events: list[str] = []

    async def fake_reset(company_id: int) -> int:
        assert company_id == 99
        events.append("reset")
        return 3

    def fake_queue_background_task(func, description, on_complete=None, on_error=None):
        events.append("queue")
        assert description == "m365-best-practices-run"

    monkeypatch.setattr(main_module, "_load_m365_best_practices_context", fake_context)
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "reset_enabled_results_to_unknown",
        fake_reset,
    )
    monkeypatch.setattr(main_module.background_tasks, "queue_background_task", fake_queue_background_task)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/m365/best-practices/run")

    assert response.status_code == 303
    assert "success=" not in response.headers["location"]
    flash_cookie = response.headers.get("set-cookie", "")
    assert "_flash=" in flash_cookie
    assert "success" in flash_cookie
    assert "Best practice evaluation started" in flash_cookie
    assert events == ["reset", "queue"]


def test_score_history_page_loads_current_company_history(monkeypatch):
    async def fake_context(request, super_admin_only=False):
        return {"id": 7, "is_super_admin": False}, {}, {"id": 99}, 99, None

    history = [{"snapshot_date": "2026-09-15"}]
    get_history = AsyncMock(return_value=history)
    render_template = AsyncMock(return_value="history-page")
    monkeypatch.setattr(main_module, "_load_m365_best_practices_context", fake_context)
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "get_daily_history",
        get_history,
    )
    monkeypatch.setattr(main_module, "_render_template", render_template)

    with TestClient(app) as client:
        response = client.get("/m365/best-practices/history")

    assert response.status_code == 200
    assert response.text == "history-page"
    get_history.assert_awaited_once_with(99)
    assert render_template.await_args.args[0] == "m365/best_practices_history.html"
    assert render_template.await_args.kwargs["extra"]["history"] == history


def test_best_practices_page_enables_note_editing_for_technician(monkeypatch):
    async def fake_context(request, super_admin_only=False):
        return {"id": 7, "is_super_admin": False}, {"role_name": "Technician"}, {"id": 99}, 99, None

    render_template = AsyncMock(return_value="bp-page")
    monkeypatch.setattr(main_module, "_load_m365_best_practices_context", fake_context)
    monkeypatch.setattr(main_module.m365_service, "get_credentials", AsyncMock(return_value={"tenant_id": "x"}))
    monkeypatch.setattr(main_module.m365_best_practices_service, "get_last_results", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.m365_best_practices_service, "get_secure_score_summary", lambda results: None)
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "list_best_practices",
        lambda: [{"id": "bp_test", "name": "Test check"}],
    )
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "get_enabled_check_ids",
        AsyncMock(return_value={"bp_test"}),
    )
    monkeypatch.setattr(main_module, "_render_template", render_template)

    with TestClient(app) as client:
        response = client.get("/m365/best-practices")

    assert response.status_code == 200
    assert render_template.await_args.kwargs["extra"]["can_edit_notes"] is True


def test_save_note_route_allows_non_super_admins(monkeypatch):
    async def fake_context(request, super_admin_only=False):
        return {"id": 7, "is_super_admin": False}, {"role_name": "Technician"}, {"id": 99}, 99, None

    set_notes = AsyncMock(return_value=True)
    monkeypatch.setattr(main_module, "_load_m365_best_practices_context", fake_context)
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "list_best_practices",
        lambda: [{"id": "bp_test", "name": "Test check"}],
    )
    monkeypatch.setattr(main_module.m365_best_practices_service, "set_result_notes", set_notes)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/m365/best-practices/note/bp_test", data={"notes": "Customer exception"})

    assert response.status_code == 303
    set_notes.assert_awaited_once_with(
        company_id=99,
        check_id="bp_test",
        notes="Customer exception",
    )


def test_save_note_route_rejects_overlong_notes(monkeypatch):
    async def fake_context(request, super_admin_only=False):
        return {"id": 7, "is_super_admin": False}, {"role_name": "Technician"}, {"id": 99}, 99, None

    monkeypatch.setattr(main_module, "_load_m365_best_practices_context", fake_context)
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "list_best_practices",
        lambda: [{"id": "bp_test", "name": "Test check"}],
    )
    set_notes = AsyncMock()
    monkeypatch.setattr(main_module.m365_best_practices_service, "set_result_notes", set_notes)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/m365/best-practices/note/bp_test", data={"notes": "x" * 4001})

    assert response.status_code == 303
    assert response.headers["location"] == "/m365/best-practices"
    assert _decode_flash_cookie(response) == {
        "message": "Check note must be 4000 characters or fewer",
        "variant": "error",
    }
    set_notes.assert_not_awaited()


def test_save_note_route_rejects_member_role(monkeypatch):
    async def fake_context(request, super_admin_only=False):
        return {"id": 7, "is_super_admin": False}, {"role_name": "Member"}, {"id": 99}, 99, None

    set_notes = AsyncMock(return_value=True)
    monkeypatch.setattr(main_module, "_load_m365_best_practices_context", fake_context)
    monkeypatch.setattr(
        main_module.m365_best_practices_service,
        "list_best_practices",
        lambda: [{"id": "bp_test", "name": "Test check"}],
    )
    monkeypatch.setattr(main_module.m365_best_practices_service, "set_result_notes", set_notes)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/m365/best-practices/note/bp_test", data={"notes": "Customer exception"})

    assert response.status_code == 303
    assert _decode_flash_cookie(response) == {
        "message": "You do not have permission to edit check notes",
        "variant": "error",
    }
    set_notes.assert_not_awaited()
