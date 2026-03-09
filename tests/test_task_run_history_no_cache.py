"""Tests that the task run history API returns no-store cache headers.

The service worker previously cached API responses, causing stale task run
history to be shown without a hard refresh. The fix limits the service worker
to only caching /static/ assets and ensures the API sets Cache-Control:
no-store so intermediary caches also bypass caching.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import scheduled_tasks as scheduled_tasks_repo


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


def _task_run_stub(task_id: int):
    from datetime import datetime, timezone

    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    return {
        "id": 1,
        "task_id": task_id,
        "task_name": "Test Task",
        "status": "succeeded",
        "started_at": now,
        "finished_at": now,
        "duration_ms": 100,
        "details": None,
    }


def test_task_runs_endpoint_returns_no_store_cache_header(monkeypatch):
    """GET /scheduler/tasks/{id}/runs must include Cache-Control: no-store."""
    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_task",
        AsyncMock(return_value={"id": 1, "name": "Test Task"}),
    )
    monkeypatch.setattr(
        scheduled_tasks_repo,
        "list_recent_runs",
        AsyncMock(return_value=[_task_run_stub(1)]),
    )

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "is_super_admin": True,
    }
    try:
        with TestClient(app) as client:
            response = client.get("/scheduler/tasks/1/runs")

        assert response.status_code == 200
        cache_control = response.headers.get("cache-control", "")
        assert "no-store" in cache_control.lower(), (
            f"Expected Cache-Control: no-store but got: {cache_control!r}"
        )
    finally:
        app.dependency_overrides.pop(database_dependencies.require_database, None)
        app.dependency_overrides.pop(auth_dependencies.require_super_admin, None)


def test_task_runs_endpoint_returns_latest_runs(monkeypatch):
    """Task runs endpoint should return the most recent runs ordered by started_at."""
    from datetime import datetime, timezone

    t1 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 1, 13, 0, tzinfo=timezone.utc)

    runs = [
        {
            "id": 2,
            "task_id": 1,
            "task_name": "Test Task",
            "status": "succeeded",
            "started_at": t2,
            "finished_at": t2,
            "duration_ms": 200,
            "details": None,
        },
        {
            "id": 1,
            "task_id": 1,
            "task_name": "Test Task",
            "status": "succeeded",
            "started_at": t1,
            "finished_at": t1,
            "duration_ms": 100,
            "details": None,
        },
    ]

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_task",
        AsyncMock(return_value={"id": 1, "name": "Test Task"}),
    )
    monkeypatch.setattr(
        scheduled_tasks_repo,
        "list_recent_runs",
        AsyncMock(return_value=runs),
    )

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "is_super_admin": True,
    }
    try:
        with TestClient(app) as client:
            response = client.get("/scheduler/tasks/1/runs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Most recent run should be first
        assert data[0]["id"] == 2
    finally:
        app.dependency_overrides.pop(database_dependencies.require_database, None)
        app.dependency_overrides.pop(auth_dependencies.require_super_admin, None)


def test_service_worker_does_not_cache_api_responses():
    """Verify the service worker source only caches /static/ paths.

    The service worker must return early (no event.respondWith) for any URL
    that is not under /static/ so that API calls are always served from the
    network and never from the service worker cache.
    """
    from pathlib import Path

    sw_path = Path(__file__).resolve().parent.parent / "app" / "static" / "service-worker.js"
    assert sw_path.is_file(), "service-worker.js not found"

    source = sw_path.read_text()

    # The guard that limits caching to /static/ must be present
    assert "pathname.startsWith('/static/')" in source, (
        "Service worker must limit caching to /static/ paths to prevent API responses "
        "from being served stale"
    )
