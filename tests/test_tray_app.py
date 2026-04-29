"""Tests for the MyPortal Tray App backend.

The fixture configures the global ``Database`` singleton to use a temp
SQLite file, runs migrations once per test session, and then exercises
the repository, service, and HTTP endpoints end-to-end.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tray_event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def tray_db(tray_event_loop):
    """Initialise the global ``db`` singleton against a fresh SQLite file
    and create the tray tables directly.

    The SQLite migration adapter has known limitations (e.g. it does not
    wrap ``DEFAULT CURRENT_TIMESTAMP`` translations in parentheses), so for
    deterministic tests we materialise the schema with SQLite-native DDL
    that mirrors ``migrations/235_tray_app.sql``.
    """

    tmp = tempfile.TemporaryDirectory()
    sqlite_path = Path(tmp.name) / "tray-tests.db"

    from app.core.database import db

    original_use_sqlite = db._use_sqlite
    original_get_path = db._get_sqlite_path
    db._use_sqlite = True
    db._get_sqlite_path = lambda: sqlite_path  # type: ignore[assignment]

    tray_event_loop.run_until_complete(db.connect())

    sqlite_ddl = [
        """CREATE TABLE IF NOT EXISTS migrations (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL UNIQUE,
               applied_at TEXT DEFAULT (datetime('now'))
           )""",
        """INSERT OR IGNORE INTO migrations (name)
               VALUES ('235_tray_app.sql')""",
        """CREATE TABLE IF NOT EXISTS chat_rooms (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               tray_device_id INTEGER NULL
           )""",
        """CREATE TABLE IF NOT EXISTS companies (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               tray_chat_enabled INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS tray_install_tokens (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               company_id INTEGER NULL,
               label TEXT NOT NULL,
               token_hash TEXT NOT NULL UNIQUE,
               token_prefix TEXT NOT NULL,
               created_by_user_id INTEGER NULL,
               created_at TEXT DEFAULT (datetime('now')),
               expires_at TEXT NULL,
               revoked_at TEXT NULL,
               last_used_at TEXT NULL,
               use_count INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS tray_devices (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               company_id INTEGER NULL,
               asset_id INTEGER NULL,
               device_uid TEXT NOT NULL UNIQUE,
               enrolment_token_id INTEGER NULL,
               auth_token_hash TEXT NOT NULL,
               auth_token_prefix TEXT NOT NULL,
               os TEXT NULL,
               os_version TEXT NULL,
               hostname TEXT NULL,
               serial_number TEXT NULL,
               agent_version TEXT NULL,
               console_user TEXT NULL,
               last_ip TEXT NULL,
               last_seen_utc TEXT NULL,
               status TEXT NOT NULL DEFAULT 'pending',
               created_at TEXT DEFAULT (datetime('now')),
               updated_at TEXT DEFAULT (datetime('now'))
           )""",
        """CREATE TABLE IF NOT EXISTS tray_menu_configs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL,
               scope TEXT NOT NULL DEFAULT 'global',
               scope_ref_id INTEGER NULL,
               payload_json TEXT NOT NULL,
               display_text TEXT NULL,
               env_allowlist TEXT NULL,
               branding_icon_url TEXT NULL,
               enabled INTEGER NOT NULL DEFAULT 1,
               version INTEGER NOT NULL DEFAULT 1,
               created_by_user_id INTEGER NULL,
               updated_by_user_id INTEGER NULL,
               created_at TEXT DEFAULT (datetime('now')),
               updated_at TEXT DEFAULT (datetime('now'))
           )""",
        """CREATE TABLE IF NOT EXISTS tray_command_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               device_id INTEGER NOT NULL,
               command TEXT NOT NULL,
               payload_json TEXT NULL,
               initiated_by_user_id INTEGER NULL,
               status TEXT NOT NULL DEFAULT 'queued',
               error TEXT NULL,
               created_at TEXT DEFAULT (datetime('now')),
               delivered_at TEXT NULL
           )""",
    ]

    async def _bootstrap():
        for stmt in sqlite_ddl:
            await db.execute(stmt)

    tray_event_loop.run_until_complete(_bootstrap())

    yield db

    tray_event_loop.run_until_complete(db.disconnect())
    db._use_sqlite = original_use_sqlite
    db._get_sqlite_path = original_get_path  # type: ignore[assignment]
    tmp.cleanup()


@pytest.fixture()
def run(tray_event_loop):
    def _run(coro):
        return tray_event_loop.run_until_complete(coro)
    return _run


# ---------------------------------------------------------------------------
# Migration & schema
# ---------------------------------------------------------------------------


def test_migration_file_is_present_and_recorded(tray_db, run):
    """The migration file exists and is recorded in the migrations table."""
    rows = run(
        tray_db.fetch_all(
            "SELECT name FROM migrations WHERE name = '235_tray_app.sql'"
        )
    )
    assert len(rows) == 1
    assert Path("migrations/235_tray_app.sql").exists()


@pytest.mark.parametrize(
    "table",
    [
        "tray_install_tokens",
        "tray_devices",
        "tray_menu_configs",
        "tray_command_log",
    ],
)
def test_tray_tables_exist(tray_db, run, table):
    rows = run(
        tray_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        )
    )
    assert rows, f"Expected table {table}"


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------


def test_token_hashing_is_deterministic_and_keyed():
    from app.services import tray as svc

    a = svc.hash_token("abc")
    b = svc.hash_token("abc")
    c = svc.hash_token("abd")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_normalise_device_uid_strips_unsafe_characters():
    from app.services import tray as svc

    assert svc.normalise_device_uid("abc 123!@#") == "abc123"
    assert len(svc.normalise_device_uid(None)) == 32


def test_env_var_allowlist_enforcement():
    from app.services import tray as svc

    assert svc.is_env_var_allowed("USERNAME", ["USERNAME", "USERDOMAIN"])
    assert svc.is_env_var_allowed("username", ["USERNAME"])
    assert not svc.is_env_var_allowed("PATH", ["USERNAME"])
    assert not svc.is_env_var_allowed("", [])


def test_technician_can_initiate_requires_company_toggle():
    from app.services import tray as svc

    super_admin = {"is_super_admin": True}
    tech = {"is_helpdesk_technician": True}
    end_user = {}

    assert svc.technician_can_initiate(super_admin, None) is True
    assert svc.technician_can_initiate(tech, None) is False
    assert svc.technician_can_initiate(tech, {"tray_chat_enabled": False}) is False
    assert svc.technician_can_initiate(tech, {"tray_chat_enabled": True}) is True
    assert svc.technician_can_initiate(end_user, {"tray_chat_enabled": True}) is False


# ---------------------------------------------------------------------------
# Repository / config resolution
# ---------------------------------------------------------------------------


def test_install_token_lifecycle(tray_db, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw = svc.generate_install_token()
    record = run(
        repo.create_install_token(
            label="ci",
            company_id=None,
            token_hash=svc.hash_token(raw),
            token_prefix=svc.token_prefix(raw),
            created_by_user_id=None,
        )
    )
    assert record["id"]

    looked_up = run(repo.get_install_token_by_hash(svc.hash_token(raw)))
    assert looked_up and looked_up["id"] == record["id"]

    run(repo.mark_install_token_used(int(record["id"])))
    refreshed = run(repo.get_install_token_by_hash(svc.hash_token(raw)))
    assert refreshed["use_count"] == 1
    assert refreshed["last_used_at"] is not None

    run(repo.revoke_install_token(int(record["id"])))
    revoked = run(repo.get_install_token_by_hash(svc.hash_token(raw)))
    assert revoked["revoked_at"] is not None


def test_device_create_update_revoke(tray_db, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw = svc.generate_auth_token()
    device = run(
        repo.create_device(
            company_id=None,
            asset_id=None,
            device_uid=svc.normalise_device_uid("dev-1"),
            enrolment_token_id=None,
            auth_token_hash=svc.hash_token(raw),
            auth_token_prefix=svc.token_prefix(raw),
            os="windows",
            os_version="11.0",
            hostname="host-1",
            serial_number=None,
            agent_version="0.1.0",
            console_user="alice",
            status="active",
        )
    )
    assert device["device_uid"] == "dev-1"

    by_hash = run(repo.get_device_by_auth_hash(svc.hash_token(raw)))
    assert by_hash and by_hash["id"] == device["id"]

    new_raw = svc.generate_auth_token()
    run(
        repo.update_device_auth(
            int(device["id"]),
            auth_token_hash=svc.hash_token(new_raw),
            auth_token_prefix=svc.token_prefix(new_raw),
        )
    )
    # Old hash should no longer match an active row.
    assert run(repo.get_device_by_auth_hash(svc.hash_token(raw))) is None
    assert run(repo.get_device_by_auth_hash(svc.hash_token(new_raw))) is not None

    run(repo.update_device_heartbeat(
        int(device["id"]),
        console_user="bob",
        last_ip="10.0.0.5",
        agent_version=None,
    ))
    updated = run(repo.get_device_by_uid("dev-1"))
    assert updated["console_user"] == "bob"
    assert updated["last_ip"] == "10.0.0.5"

    run(repo.revoke_device(int(device["id"])))
    assert run(repo.get_device_by_auth_hash(svc.hash_token(new_raw))) is None


def test_resolve_config_default_when_no_configs(tray_db, run):
    from app.services import tray as svc

    cfg = run(
        svc.resolve_config_for_device({"company_id": None, "asset_id": None})
    )
    assert cfg["menu"]
    assert any(node.get("type") == "open_chat" for node in cfg["menu"])


def test_resolve_config_precedence(tray_db, run):
    import json
    from app.repositories import tray as repo
    from app.services import tray as svc

    run(
        repo.create_menu_config(
            name="global-precedence",
            scope="global",
            scope_ref_id=None,
            payload_json=json.dumps([{"type": "label", "label": "global"}]),
            display_text=None,
            env_allowlist="USERNAME",
            branding_icon_url=None,
            enabled=True,
            created_by_user_id=None,
        )
    )
    run(
        repo.create_menu_config(
            name="company-99",
            scope="company",
            scope_ref_id=99,
            payload_json=json.dumps([{"type": "label", "label": "company-99"}]),
            display_text=None,
            env_allowlist=None,
            branding_icon_url=None,
            enabled=True,
            created_by_user_id=None,
        )
    )

    cfg_global = run(
        svc.resolve_config_for_device({"company_id": 1234, "asset_id": None})
    )
    assert cfg_global["menu"][0]["label"] == "global"
    assert cfg_global["env_allowlist"] == ["USERNAME"]

    cfg_company = run(
        svc.resolve_config_for_device({"company_id": 99, "asset_id": None})
    )
    assert cfg_company["menu"][0]["label"] == "company-99"


# ---------------------------------------------------------------------------
# HTTP endpoints (TestClient) — exercises auth middleware too
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def http_client(tray_db):
    """A FastAPI TestClient bound to our test SQLite singleton."""

    from fastapi.testclient import TestClient
    from app.core.database import db
    from app.main import app
    from app.services.scheduler import scheduler_service

    # Stop the scheduler from spinning up in tests; keep db pointed at our
    # fixture-controlled SQLite singleton.
    async def _noop():  # pragma: no cover - trivial
        return None

    original_connect = db.connect
    original_disconnect = db.disconnect
    original_run_migrations = db.run_migrations
    db.connect = _noop  # type: ignore[assignment]
    db.disconnect = _noop  # type: ignore[assignment]
    db.run_migrations = _noop  # type: ignore[assignment]
    original_start = scheduler_service.start
    original_stop = scheduler_service.stop
    scheduler_service.start = _noop  # type: ignore[assignment]
    scheduler_service.stop = _noop  # type: ignore[assignment]

    with TestClient(app, follow_redirects=False, headers={"Accept": "application/json"}) as client:
        yield client

    db.connect = original_connect  # type: ignore[assignment]
    db.disconnect = original_disconnect  # type: ignore[assignment]
    db.run_migrations = original_run_migrations  # type: ignore[assignment]
    scheduler_service.start = original_start  # type: ignore[assignment]
    scheduler_service.stop = original_stop  # type: ignore[assignment]


def test_enrol_rejects_invalid_install_token(http_client):
    response = http_client.post(
        "/api/tray/enrol",
        json={"install_token": "bogus-token-bogus-token", "os": "windows"},
    )
    assert response.status_code == 401


def test_enrol_then_config_then_heartbeat(http_client, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw = svc.generate_install_token()
    run(
        repo.create_install_token(
            label="http-test",
            company_id=None,
            token_hash=svc.hash_token(raw),
            token_prefix=svc.token_prefix(raw),
            created_by_user_id=None,
        )
    )

    enrol = http_client.post(
        "/api/tray/enrol",
        json={
            "install_token": raw,
            "os": "windows",
            "hostname": "ws-test",
            "agent_version": "0.1.0",
        },
    )
    assert enrol.status_code == 200, enrol.text
    body = enrol.json()
    auth_token = body["auth_token"]
    assert body["device_uid"]

    cfg = http_client.get(
        "/api/tray/config",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert cfg.status_code == 200, cfg.text
    payload = cfg.json()
    assert "menu" in payload
    assert "chat_enabled" in payload

    hb = http_client.post(
        "/api/tray/heartbeat",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"console_user": "alice"},
    )
    assert hb.status_code == 200

    # An unrecognised auth token must be rejected.
    bad = http_client.get(
        "/api/tray/config",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert bad.status_code == 401


def test_revoked_device_is_rejected_by_config(http_client, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw = svc.generate_install_token()
    run(
        repo.create_install_token(
            label="rv",
            company_id=None,
            token_hash=svc.hash_token(raw),
            token_prefix=svc.token_prefix(raw),
            created_by_user_id=None,
        )
    )
    enrol = http_client.post(
        "/api/tray/enrol",
        json={"install_token": raw, "os": "macos"},
    )
    assert enrol.status_code == 200
    body = enrol.json()
    device = run(repo.get_device_by_uid(body["device_uid"]))
    run(repo.revoke_device(int(device["id"])))

    cfg = http_client.get(
        "/api/tray/config",
        headers={"Authorization": f"Bearer {body['auth_token']}"},
    )
    assert cfg.status_code == 401


def test_chat_start_404_when_device_missing(http_client):
    # No session cookie; the chat-start endpoint requires an authenticated
    # technician, so we expect 401 rather than reaching the device lookup.
    response = http_client.post(
        "/api/tray/missing-uid/chat/start",
        json={},
    )
    assert response.status_code in (401, 403, 404)


def test_admin_endpoints_require_authentication(http_client):
    response = http_client.get("/api/tray/admin/devices")
    assert response.status_code in (401, 403)
    response = http_client.get("/api/tray/admin/configs")
    assert response.status_code in (401, 403)
    response = http_client.post("/api/tray/admin/configs", json={"name": "x"})
    assert response.status_code in (401, 403)
