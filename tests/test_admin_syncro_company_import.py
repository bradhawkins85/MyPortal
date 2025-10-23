from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

import app.main as main_module
from app.core.database import db
from app.main import app, company_importer, scheduler_service


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

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "acquire", fake_acquire)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
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
    async def fake_import_all_companies():
        return DummySummary(fetched=2, created=1, updated=1, skipped=0)

    monkeypatch.setattr(company_importer, "import_all_companies", fake_import_all_companies)

    with TestClient(app) as client:
        response = client.post(
            "/admin/syncro/import-companies",
            headers={"accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == {"fetched": 2, "created": 1, "updated": 1, "skipped": 0}


def test_import_companies_form_redirect(monkeypatch):
    async def fake_import_all_companies():
        return DummySummary(fetched=1, created=1, updated=0, skipped=0)

    monkeypatch.setattr(company_importer, "import_all_companies", fake_import_all_companies)

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
    assert params["success"] == ["Imported 1 company (created 1, updated 0, skipped 0)."]
