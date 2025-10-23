from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import pytest
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.services import background as background_tasks
from app.services import change_log as change_log_service


class DummySummary:
    def __init__(self, *, fetched: int, created: int, updated: int, skipped: int):
        self.fetched = fetched
        self.created = created
        self.updated = updated
        self.skipped = skipped

    def as_dict(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


@pytest.fixture(autouse=True)
def disable_startup(monkeypatch):
    class DummyCursor:
        async def execute(self, *args, **kwargs):
            return None

        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

        @property
        def lastrowid(self):  # type: ignore[override]
            return 0

    class DummyCursorContext:
        async def __aenter__(self):
            return DummyCursor()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyConnection:
        def cursor(self, *args, **kwargs):
            return DummyCursorContext()

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

    async def fake_sync_change_log_sources(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "acquire", fake_acquire)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(
        change_log_service, "sync_change_log_sources", fake_sync_change_log_sources
    )
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


@pytest.fixture(autouse=True)
def super_admin_context(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_load_syncro_module():
        return {"enabled": True}

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(main_module, "_load_syncro_module", fake_load_syncro_module)


def test_import_companies_returns_json(monkeypatch):
    captured: dict[str, str] = {}

    def fake_queue(task_factory, *, task_id=None, description=None, on_complete=None, on_error=None):
        assigned = task_id or "queued-task-id"
        captured["task_id"] = assigned
        if on_complete:
            on_complete(DummySummary(fetched=2, created=1, updated=1, skipped=0))
        return assigned

    monkeypatch.setattr(background_tasks, "queue_background_task", fake_queue)

    with TestClient(app) as client:
        response = client.post(
            "/admin/syncro/import-companies",
            headers={"accept": "application/json"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload == {
        "status": "queued",
        "taskId": captured["task_id"],
        "message": "Syncro company import queued.",
    }


def test_import_companies_form_redirect(monkeypatch):
    captured: dict[str, str] = {}

    def fake_queue(task_factory, *, task_id=None, description=None, on_complete=None, on_error=None):
        assigned = task_id or "abc12345def"
        captured["task_id"] = assigned
        if on_complete:
            on_complete(DummySummary(fetched=1, created=1, updated=0, skipped=0))
        return assigned

    monkeypatch.setattr(background_tasks, "queue_background_task", fake_queue)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/admin/syncro/import-companies",
            headers={"accept": "text/html"},
            data={},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    assert parsed.path == "/admin/companies/syncro-import"
    params = parse_qs(parsed.query)
    expected_task_id = captured["task_id"][:8]
    assert params["success"] == [f"Syncro company import queued. Task ID: {expected_task_id}"]
