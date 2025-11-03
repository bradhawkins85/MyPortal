import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.services import modules as modules_service


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


def test_xero_callback_accepts_payload(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        assert slug == "xero"
        assert redact is False
        return {"enabled": True, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/xero/callback",
            json={"events": []},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_xero_callback_rejects_invalid_json(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/xero/callback",
            data="{",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON payload"


def test_xero_callback_disabled_module(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": False, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/xero/callback",
            json={},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Xero module is disabled"


def test_xero_callback_probe(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.get("/api/integration-modules/xero/callback")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
