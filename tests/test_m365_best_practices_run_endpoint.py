from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.services.m365_best_practices as bp_service
from app.core.database import db
from app.main import app, scheduler_service


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
    assert "success=Best+practice+evaluation+started+in+the+background" in response.headers["location"]
    assert events == ["reset", "queue"]
