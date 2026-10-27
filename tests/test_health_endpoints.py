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
    monkeypatch.setattr(main_module, "_database_last_ok", None)


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
        assert body["version"] == main_module._APP_VERSION
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


def _ready_without_startup(monkeypatch) -> TestClient:
    async def _ok(*args, **kwargs):
        return {"1": 1}

    monkeypatch.setattr(db, "fetch_one", _ok)
    monkeypatch.setattr(main_module, "_app_ready", True)
    # The non-context-manager form does not run startup events.
    return TestClient(app)


def test_readyz_waits_for_every_worker(monkeypatch):
    client = _ready_without_startup(monkeypatch)
    monkeypatch.setattr(main_module.worker_readiness, "status", lambda: "pending: 1 of 4 workers started")
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["workers"] == "pending: 1 of 4 workers started"

    monkeypatch.setattr(main_module.worker_readiness, "status", lambda: "ok")
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["workers"] == "ok"


def test_readyz_omits_workers_for_a_single_process(monkeypatch):
    resp = _ready_without_startup(monkeypatch).get("/readyz")
    assert resp.status_code == 200
    assert "workers" not in resp.json()["checks"]


def test_readyz_answers_quickly_when_the_database_hangs(monkeypatch):
    # Load balancers time out slow probes and drop the server; a hung pool or
    # query must fail the check well inside their timeout instead.
    import asyncio

    async def _hang(*args, **kwargs):
        await asyncio.sleep(60)

    client = _ready_without_startup(monkeypatch)
    monkeypatch.setattr(db, "fetch_one", _hang)
    monkeypatch.setattr(main_module, "_READYZ_DATABASE_TIMEOUT", 0.05)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "error: Timeout"


def test_readyz_rides_out_a_brief_database_failure(monkeypatch):
    client = _ready_without_startup(monkeypatch)
    assert client.get("/readyz").json()["status"] == "ok"

    async def _boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(db, "fetch_one", _boom)
    resp = client.get("/readyz")
    # Still routable, but not "ok", so upgrades do not cut over to it.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "error: RuntimeError"

    # An outage longer than the grace period takes it out of rotation.
    monkeypatch.setattr(main_module, "_READYZ_DATABASE_GRACE", 0.0)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
