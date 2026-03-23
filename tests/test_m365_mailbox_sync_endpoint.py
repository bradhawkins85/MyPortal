"""Tests for the async /m365/mailboxes/sync endpoint.

Covers:
- Endpoint returns 202 and queues the scheduled task when sync_m365_data task exists.
- Endpoint falls back to queuing sync_mailboxes directly when no task exists.
- Non-super-admin receives 403 Forbidden.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service

_JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


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


def _super_admin_context():
    async def fake_load_license_context(request, **kwargs):
        user = {"id": 1, "is_super_admin": True, "company_id": 42}
        return user, None, None, 42, None

    return fake_load_license_context


def _non_admin_context():
    async def fake_load_license_context(request, **kwargs):
        user = {"id": 2, "is_super_admin": False, "company_id": 42}
        return user, None, None, 42, None

    return fake_load_license_context


# ---------------------------------------------------------------------------
# sync endpoint – happy path: scheduled task found
# ---------------------------------------------------------------------------


def test_sync_endpoint_queues_scheduled_task_and_returns_202(monkeypatch):
    """When a sync_m365_data task exists the endpoint returns 202 immediately."""
    fake_task = {"id": 7, "command": "sync_m365_data", "company_id": 42}

    async def fake_get_task(company_id, command):
        if command == "sync_m365_data":
            return fake_task
        return None

    # Capture coroutines passed to create_task; close them to avoid "never awaited" warnings.
    queued_coros = []

    def fake_create_task(coro):
        queued_coros.append(coro.__name__ if hasattr(coro, "__name__") else str(coro))
        coro.close()
        return MagicMock()

    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task_for_company_by_command", fake_get_task)
    monkeypatch.setattr(main_module.asyncio, "create_task", fake_create_task)

    with TestClient(app) as client:
        response = client.post("/m365/mailboxes/sync", headers=_JSON_HEADERS)

    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert len(queued_coros) == 1


# ---------------------------------------------------------------------------
# sync endpoint – fallback: no scheduled task exists
# ---------------------------------------------------------------------------


def test_sync_endpoint_falls_back_to_direct_sync_when_no_task(monkeypatch):
    """When no sync_m365_data / sync_o365 task exists it falls back to sync_mailboxes."""

    async def fake_get_task(company_id, command):
        return None

    queued_coros = []

    def fake_create_task(coro):
        queued_coros.append(coro.__name__ if hasattr(coro, "__name__") else str(coro))
        coro.close()
        return MagicMock()

    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "get_task_for_company_by_command", fake_get_task)
    monkeypatch.setattr(main_module.asyncio, "create_task", fake_create_task)

    with TestClient(app) as client:
        response = client.post("/m365/mailboxes/sync", headers=_JSON_HEADERS)

    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert len(queued_coros) == 1


# ---------------------------------------------------------------------------
# sync endpoint – non-super-admin is rejected
# ---------------------------------------------------------------------------


def test_sync_endpoint_rejects_non_super_admin(monkeypatch):
    """Non-super-admin users receive 403 Forbidden."""
    monkeypatch.setattr(main_module, "_load_license_context", _non_admin_context())

    with TestClient(app) as client:
        # Send JSON headers so the error handler returns JSON (not an HTML error page
        # which would attempt DB access and fail in the test environment).
        response = client.post("/m365/mailboxes/sync", headers=_JSON_HEADERS)

    assert response.status_code == 403
