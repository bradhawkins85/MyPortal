"""Tests for the durable /m365/mailboxes/sync endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

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
    monkeypatch.setattr(main_module.m365_jobs_service, "start_worker", lambda: None)
    monkeypatch.setattr(main_module.m365_jobs_service, "stop_worker", fake_stop)
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
# sync endpoint – happy path: sync_m365_mailboxes task found
# ---------------------------------------------------------------------------


def test_sync_endpoint_returns_durable_job_id(monkeypatch):
    """The accepted response identifies the persisted operation."""
    enqueue = AsyncMock(return_value={"id": "job-123", "status": "queued"})

    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(main_module.m365_jobs_service, "enqueue", enqueue)

    with TestClient(app) as client:
        response = client.post("/m365/mailboxes/sync", headers=_JSON_HEADERS)

    assert response.status_code == 202
    assert response.json() == {"job_id": "job-123", "status": "queued"}
    enqueue.assert_awaited_once_with(42, "mailbox_sync", "mailboxes")


# ---------------------------------------------------------------------------
# sync endpoint – fallback: no scheduled task exists
# ---------------------------------------------------------------------------


def test_sync_endpoint_returns_existing_active_job(monkeypatch):
    """A double click follows the same active operation rather than duplicating it."""
    enqueue = AsyncMock(return_value={"id": "existing-job", "status": "running"})

    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(main_module.m365_jobs_service, "enqueue", enqueue)

    with TestClient(app) as client:
        response = client.post("/m365/mailboxes/sync", headers=_JSON_HEADERS)

    assert response.status_code == 202
    assert response.json() == {"job_id": "existing-job", "status": "running"}


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
