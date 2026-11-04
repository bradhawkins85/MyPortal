"""Overseer reports: connectivity ages, version tracking and audited recoveries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import auth
from app.api.routes import tray as tray_routes
from app.services import tray_overseer
from tests.test_rmm_scripts import _enrol, anyio_backend, sqlite_db  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)


def test_connectivity_reports_server_measured_ages():
    device = {"id": 1, "ws_last_seen_utc": (NOW - timedelta(seconds=40)).replace(tzinfo=None)}
    agent = {"id": 9, "last_seen_utc": (NOW - timedelta(minutes=20)).isoformat()}
    status = tray_overseer.connectivity(device, agent, now=NOW)
    assert status["tray"] == {"enrolled": True, "last_seen_age_seconds": 40}
    assert status["rmm"] == {"enrolled": True, "last_seen_age_seconds": 1200}
    assert status["check_interval_seconds"] == tray_overseer.CHECK_INTERVAL_SECONDS


def test_connectivity_without_websocket_or_rmm_agent():
    status = tray_overseer.connectivity({"id": 1, "ws_last_seen_utc": None}, None, now=NOW)
    assert status["tray"] == {"enrolled": True, "last_seen_age_seconds": None}
    assert status["rmm"] == {"enrolled": False, "last_seen_age_seconds": None}


def test_age_never_negative_for_future_timestamps():
    assert tray_overseer.age_seconds(NOW + timedelta(minutes=1), now=NOW) == 0
    assert tray_overseer.age_seconds("not a date", now=NOW) is None


@pytest.fixture
def client(monkeypatch):
    recorded: dict = {"reports": [], "audits": []}
    device = {"id": 7, "company_id": 3, "asset_id": 11, "ws_last_seen_utc": None}

    async def record_overseer_report(device_id, *, overseer_version):
        recorded["reports"].append((device_id, overseer_version))

    async def get_tray_device_agent(tray_device_id):
        assert tray_device_id == 7
        return {"id": 2, "last_seen_utc": datetime.now(timezone.utc).replace(tzinfo=None)}

    async def audit_record(**kwargs):
        recorded["audits"].append(kwargs)

    monkeypatch.setattr(tray_overseer.tray_repo, "record_overseer_report", record_overseer_report)
    monkeypatch.setattr(tray_overseer.rmm_repo, "get_tray_device_agent", get_tray_device_agent)
    monkeypatch.setattr(tray_overseer.audit_service, "record", audit_record)

    async def current_device():
        return device

    app = FastAPI()
    app.include_router(tray_routes.router)
    app.dependency_overrides[auth.get_current_tray_device] = current_device
    with TestClient(app) as test_client:
        yield test_client, recorded


def _path() -> str:
    prefix = tray_routes.router.prefix or ""
    return prefix + "/overseer/report"


def test_report_records_version_audits_recoveries_and_returns_status(client):
    test_client, recorded = client
    response = test_client.post(
        _path(),
        json={
            "overseer_version": "2.4.0",
            "services": [{"service": "tray", "installed": True, "enabled": True, "running": True}],
            "actions": [{
                "service": "rmm", "action": "restart", "reason": "MyPortal last heard from the RMM agent 20m0s ago",
                "occurred_at": "2026-11-04T09:55:00Z", "succeeded": True,
            }],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tray"] == {"enrolled": True, "last_seen_age_seconds": None}
    assert body["rmm"]["enrolled"] is True and body["rmm"]["last_seen_age_seconds"] < 60
    assert body["check_interval_seconds"] == tray_overseer.CHECK_INTERVAL_SECONDS
    assert recorded["reports"] == [(7, "2.4.0")]
    [audit] = recorded["audits"]
    assert audit["action"] == "tray.overseer.recover"
    assert audit["entity_type"] == "tray_device" and audit["entity_id"] == 7
    assert audit["actor"] == "overseer"
    assert audit["metadata"]["service"] == "rmm" and audit["metadata"]["recovery"] == "restart"
    assert audit["metadata"]["company_id"] == 3 and audit["metadata"]["succeeded"] is True


def test_report_rejects_unknown_services_and_too_many_actions(client):
    test_client, recorded = client
    bad_service = {"actions": [{"service": "other", "action": "restart"}]}
    assert test_client.post(_path(), json=bad_service).status_code == 422
    too_many = {"actions": [{"service": "tray", "action": "start"}] * 21}
    assert test_client.post(_path(), json=too_many).status_code == 422
    assert recorded["reports"] == []


def test_report_requires_tray_device_token():
    app = FastAPI()
    app.include_router(tray_routes.router)
    with TestClient(app) as test_client:
        assert test_client.post(_path(), json={}).status_code == 401


def test_migration_adds_overseer_columns():
    sql = (Path(__file__).parent.parent / "migrations" / "541_tray_device_overseer.sql").read_text()
    assert "-- phase: expand" in sql
    assert "ADD COLUMN IF NOT EXISTS overseer_version" in sql
    assert "ADD COLUMN IF NOT EXISTS overseer_last_seen_utc" in sql


@pytest.mark.anyio
async def test_report_against_sqlite_records_overseer_and_finds_rmm_agent(sqlite_db, monkeypatch):
    from app.core.database import Database

    sql = Database()._adapt_sql_for_sqlite(
        (Path(__file__).parent.parent / "migrations" / "541_tray_device_overseer.sql").read_text()
    )
    await sqlite_db.conn.executescript(
        "ALTER TABLE tray_devices ADD COLUMN ws_last_seen_utc TEXT;"
        "INSERT INTO tray_devices (id, company_id, asset_id, device_uid) VALUES (5, 1, 10, 'dev-5');"
    )
    await sqlite_db.conn.executescript(sql)
    monkeypatch.setattr(sqlite_db, "is_sqlite", lambda: True, raising=False)
    audits: list = []

    async def audit_record(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(tray_overseer.audit_service, "record", audit_record)

    device = await sqlite_db.fetch_one("SELECT * FROM tray_devices WHERE id = 5")
    status = await tray_overseer.record_report(device, {"overseer_version": "1.2.3", "actions": []})
    assert status["rmm"] == {"enrolled": False, "last_seen_age_seconds": None}

    await _enrol(sqlite_db, tray_id=5)
    status = await tray_overseer.record_report(device, {"overseer_version": None, "actions": []})
    assert status["rmm"]["enrolled"] is True
    row = await sqlite_db.fetch_one("SELECT overseer_version, overseer_last_seen_utc FROM tray_devices WHERE id = 5")
    assert row["overseer_version"] == "1.2.3" and row["overseer_last_seen_utc"]
    assert audits == []
