"""Tests for the /healthz and /readyz endpoints.

* ``/healthz`` must succeed unconditionally — it's the cheap liveness
  probe used by nginx / systemd.
* ``/readyz`` returns 503 until ``_app_ready`` is true *and* the
  database responds.  When the app is ready and the DB is healthy it
  must return 200 with per-check breakdown.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop():
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)


def test_healthz_always_ok():
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_readyz_when_db_healthy(monkeypatch):
    async def _ok(*args, **kwargs):
        return {"1": 1}

    monkeypatch.setattr(db, "fetch_one", _ok)
    with TestClient(app) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["startup"] == "ok"
        assert body["checks"]["feature_packs"] == "ok"


def test_readyz_returns_503_when_db_down(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(db, "fetch_one", _boom)
    with TestClient(app) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"].startswith("error:")


def test_readyz_returns_503_before_startup(monkeypatch):
    # Force the readiness flag off without going through TestClient
    # (which would run startup events).
    monkeypatch.setattr(main_module, "_app_ready", False)
    client = TestClient(app)
    # Use the non-context-manager form so startup events do not run.
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["startup"] == "pending"
