"""Server-side asset provisioning without changing installed tray clients."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import aiosqlite

from app.core.database import Database
from app.repositories import assets, tray, rmm
from app.services import tray as service


class SqliteDb(Database):
    """Use the SQL placeholder adapter used by the RMM repository tests."""
    async def execute(self, sql, params=None):
        return await super().execute(sql.replace("%s", "?"), params)

    async def fetch_one(self, sql, params=None):
        return await super().fetch_one(sql.replace("%s", "?"), params)

    async def fetch_all(self, sql, params=None):
        return await super().fetch_all(sql.replace("%s", "?"), params)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def asset_db(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    adapter = SqliteDb()
    adapter._use_sqlite = True
    adapter._sqlite_conn = conn
    for module in (assets, tray, rmm):
        monkeypatch.setattr(module, "db", adapter)
    await conn.executescript("""
        CREATE TABLE assets (
          id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INT NOT NULL, name TEXT,
          type TEXT, asset_type TEXT, asset_type_source TEXT, machine_type TEXT,
          serial_number TEXT, status TEXT, os_name TEXT, cpu_name TEXT, ram_gb REAL,
          hdd_size TEXT, last_sync TEXT, boot_time TEXT, motherboard_manufacturer TEXT,
          form_factor TEXT, last_user TEXT, approx_age REAL, performance_score REAL,
          warranty_status TEXT, warranty_end_date TEXT, warranty_start_date TEXT,
          warranty_source TEXT, syncro_asset_id TEXT, tactical_asset_id TEXT,
          mac_address TEXT, customer_visible INT DEFAULT 1, provenance TEXT,
          archived_at TEXT, location TEXT);
        CREATE TABLE tray_devices (
          id INTEGER PRIMARY KEY, company_id INT, asset_id INT, device_uid TEXT UNIQUE,
          os TEXT, os_version TEXT, hostname TEXT, serial_number TEXT,
          console_user TEXT, status TEXT, last_seen_utc TEXT, created_at TEXT);
        CREATE TABLE rmm_agents (
          id INTEGER PRIMARY KEY, tray_device_id INT, company_id INT, asset_id INT,
          status TEXT, last_seen_utc TEXT);
    """)
    migration = Path(__file__).resolve().parents[1] / "migrations/485_tray_device_assets.sql"
    await conn.executescript(adapter._adapt_sql_for_sqlite(migration.read_text()))
    async def refresh_tags(asset_id):
        pass
    monkeypatch.setattr(assets, "_refresh_auto_tags", refresh_tags)
    yield adapter
    await conn.close()


async def add_device(db, *, uid="tray-0001", company=1, asset_id=None, serial="SN-1", hostname="PC-1", status="active"):
    await db.execute(
        "INSERT INTO tray_devices (company_id, asset_id, device_uid, os, os_version, "
        "hostname, serial_number, console_user, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (company, asset_id, uid, "windows", "11", hostname, serial, "alice", status),
    )
    return await tray.get_device_by_uid(uid)


@pytest.mark.anyio
async def test_backfill_creates_asset_and_links_existing_rmm_for_scripts(asset_db):
    device = await add_device(asset_db)
    await asset_db.execute(
        "INSERT INTO rmm_agents VALUES (1, %s, 1, NULL, 'active', NULL)", (device["id"],)
    )
    await service.reconcile_device_assets()
    linked = await tray.get_device_by_uid(device["device_uid"])
    asset = await assets.get_asset_by_id(linked["asset_id"])
    assert (asset["company_id"], asset["name"], asset["serial_number"]) == (1, "PC-1", "SN-1")
    assert (asset["os_name"], asset["last_user"], asset["type"]) == ("windows 11", "alice", "Workstation")
    # The same lookup used to target scripts from the asset page now finds it.
    assert (await rmm.get_asset_agent(asset["id"]))["id"] == 1
    await service.reconcile_device_assets()
    assert len(await assets.list_company_assets(1)) == 1


@pytest.mark.anyio
async def test_existing_asset_and_rmm_links_are_preserved(asset_db):
    await asset_db.execute("INSERT INTO assets (id,company_id,name,serial_number) VALUES (10,1,'Inventory PC','SN-1')")
    device = await add_device(asset_db)
    assert await service.ensure_device_asset(device) == 10
    assert len(await assets.list_company_assets(1)) == 1
    # An explicit device association takes precedence over changed host facts.
    device["serial_number"] = "different"
    assert await service.ensure_device_asset(device) == 10


@pytest.mark.anyio
async def test_reuses_rmm_asset_before_creating_duplicate(asset_db):
    await asset_db.execute("INSERT INTO assets (id,company_id,name) VALUES (10,1,'Older name')")
    device = await add_device(asset_db)
    await asset_db.execute("INSERT INTO rmm_agents VALUES (1,%s,1,10,'active',NULL)", (device["id"],))
    assert await service.ensure_device_asset(device) == 10
    assert len(await assets.list_company_assets(1)) == 1


@pytest.mark.anyio
async def test_existing_tray_and_rmm_asset_links_are_aligned(asset_db):
    await asset_db.execute("INSERT INTO assets (id,company_id,name) VALUES (10,1,'Canonical'), (11,1,'Old RMM link')")
    device = await add_device(asset_db, asset_id=10)
    await asset_db.execute("INSERT INTO rmm_agents VALUES (1,%s,1,11,'active',NULL)", (device["id"],))
    assert await service.ensure_device_asset(device) == 10
    assert (await rmm.get_asset_agent(10))["id"] == 1
    assert await assets.get_asset_by_id(11)


@pytest.mark.anyio
async def test_creation_retries_after_link_failure_do_not_duplicate_assets(asset_db, monkeypatch):
    device = await add_device(asset_db)
    original = tray.link_device_to_asset
    async def fail_link(*args):
        raise RuntimeError("connection interrupted")
    monkeypatch.setattr(tray, "link_device_to_asset", fail_link)
    with pytest.raises(RuntimeError):
        await service.ensure_device_asset(device)
    monkeypatch.setattr(tray, "link_device_to_asset", original)
    await service.ensure_device_asset(device)
    assert len(await assets.list_company_assets(1)) == 1
    # The unique identity also protects repeated insert attempts directly.
    assert await assets.create_tray_asset(device) == device["asset_id"]


@pytest.mark.anyio
async def test_concurrent_creation_uses_single_asset_identity(asset_db):
    device = await add_device(asset_db)
    ids = await asyncio.gather(*(assets.create_tray_asset(dict(device)) for _ in range(5)))
    assert len(set(ids)) == 1
    assert len(await assets.list_company_assets(1)) == 1


@pytest.mark.anyio
async def test_backfill_continues_after_one_device_fails(asset_db, monkeypatch):
    await add_device(asset_db, uid="tray-bad")
    await add_device(asset_db, uid="tray-good", hostname="GOOD", serial="SN-GOOD")
    original = assets.create_tray_asset
    async def create(device):
        if device["device_uid"] == "tray-bad":
            raise RuntimeError("temporary failure")
        return await original(device)
    monkeypatch.setattr(assets, "create_tray_asset", create)
    await service.reconcile_device_assets()
    assert (await tray.get_device_by_uid("tray-bad"))["asset_id"] is None
    assert (await tray.get_device_by_uid("tray-good"))["asset_id"]


@pytest.mark.anyio
async def test_foreign_or_missing_links_are_repaired_within_company(asset_db):
    await asset_db.execute("INSERT INTO assets (id,company_id,name,serial_number) VALUES (10,2,'PC-1','SN-1')")
    device = await add_device(asset_db, asset_id=10)
    await asset_db.execute("INSERT INTO rmm_agents VALUES (1,%s,1,10,'active',NULL)", (device["id"],))
    await asset_db.execute("INSERT INTO rmm_agents VALUES (2,%s,2,10,'active',NULL)", (device["id"],))
    linked_id = await service.ensure_device_asset(device)
    assert linked_id != 10
    assert (await rmm.get_asset_agent(linked_id))["company_id"] == 1
    assert (await asset_db.fetch_one("SELECT asset_id FROM rmm_agents WHERE id=2"))["asset_id"] == 10
    await asset_db.execute("DELETE FROM assets WHERE id=%s", (linked_id,))
    assert await service.ensure_device_asset(device) != linked_id


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [{"company": None}, {"status": "revoked"}])
async def test_unassigned_and_revoked_devices_do_not_create_assets(asset_db, kwargs):
    device = await add_device(asset_db, **kwargs)
    assert await service.ensure_device_asset(device) is None
    assert not await asset_db.fetch_all("SELECT * FROM assets")


@pytest.mark.anyio
async def test_placeholder_serial_does_not_link_unrelated_device(asset_db):
    await asset_db.execute("INSERT INTO assets (id,company_id,name,serial_number) VALUES (10,1,'OTHER','To Be Filled By O.E.M.')")
    device = await add_device(asset_db, serial="To Be Filled By O.E.M.")
    asset_id = await service.ensure_device_asset(device)
    assert asset_id != 10
    assert (await assets.get_asset_by_id(asset_id))["serial_number"] is None


@pytest.mark.anyio
async def test_new_enrollment_returns_provisioned_asset(asset_db, monkeypatch):
    from app.api.routes import tray as routes
    from app.schemas.tray import TrayEnrolRequest
    async def token_lookup(token_hash):
        return {"id": 1, "company_id": 1}
    async def create_device(**kwargs):
        return await add_device(asset_db, uid=kwargs["device_uid"], hostname=kwargs["hostname"])
    async def used(token_id):
        pass
    monkeypatch.setattr(tray, "get_install_token_by_hash", token_lookup)
    monkeypatch.setattr(tray, "create_device", create_device)
    monkeypatch.setattr(tray, "mark_install_token_used", used)
    response = await routes.enrol_device(
        TrayEnrolRequest(install_token="a" * 32, device_uid="tray-new", os="windows", hostname="NEW-PC"),
        SimpleNamespace(),
    )
    assert response.asset_id
    assert (await assets.get_asset_by_id(response.asset_id))["name"] == "NEW-PC"
    assert (await tray.get_device_by_uid("tray-new"))["asset_id"] == response.asset_id
    async def update_auth(*args, **kwargs):
        pass
    monkeypatch.setattr(tray, "update_device_auth", update_auth)
    again = await routes.enrol_device(
        TrayEnrolRequest(install_token="a" * 32, device_uid="tray-new", os="windows", hostname="NEW-PC"),
        SimpleNamespace(),
    )
    assert again.asset_id == response.asset_id
    assert len(await assets.list_company_assets(1)) == 1


@pytest.fixture
async def setup_ticket_db(asset_db, monkeypatch):
    from app.repositories import companies, tickets
    from app.services import tickets as ticket_service
    from unittest.mock import AsyncMock

    for module in (companies, tickets):
        monkeypatch.setattr(module, "db", asset_db)
    await asset_db._sqlite_conn.executescript("""
        CREATE TABLE companies (id INT PRIMARY KEY, name TEXT);
        CREATE TABLE company_email_domains (company_id INT, domain TEXT);
        CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,
          company_id INT, subject TEXT, description TEXT, external_reference TEXT,
          status TEXT, priority TEXT);
        CREATE TABLE ticket_assets (ticket_id INT, asset_id INT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(ticket_id, asset_id));
        ALTER TABLE tray_devices ADD COLUMN updated_at TEXT;
    """)
    # Verify the migration excludes devices which checked in before deployment.
    await add_device(asset_db, uid="existing-device")
    migration = Path(__file__).resolve().parents[1] / "migrations/490_tray_setup_tickets.sql"
    await asset_db._sqlite_conn.executescript(asset_db._adapt_sql_for_sqlite(migration.read_text()))
    await asset_db.execute("INSERT INTO companies (id,name) VALUES (1,'ACME'),(2,'Other')")

    async def create_ticket(**kwargs):
        ticket_id = await asset_db.execute_returning_lastrowid(
            "INSERT INTO tickets (company_id,subject,description,external_reference,status,priority) "
            "VALUES (?,?,?,?,?,?)",
            tuple(kwargs[key] for key in (
                "company_id", "subject", "description", "external_reference", "status", "priority"
            )),
        )
        return await tickets.get_ticket(ticket_id)

    # Exercise real ticket persistence/lookups and asset associations without
    # unrelated notification/automation infrastructure in this focused fixture.
    create = AsyncMock(side_effect=create_ticket)
    monkeypatch.setattr(ticket_service, "create_ticket", create)
    yield asset_db, create


@pytest.mark.anyio
@pytest.mark.parametrize("hostname", ["PC-1", "x" * 255])
async def test_setup_ticket_is_company_owned_and_asset_linked_once(setup_ticket_db, hostname):
    db, create = setup_ticket_db
    device = await add_device(db, uid="new-device", company=2, hostname=hostname)
    # Separate request dictionaries simulate concurrent check-ins/re-enrolment.
    await asyncio.gather(*(service.ensure_device_setup_ticket(dict(device)) for _ in range(5)))
    await service.ensure_device_setup_ticket(device)
    create.assert_awaited_once()
    ticket = await db.fetch_one("SELECT * FROM tickets")
    linked = await tray.get_device_by_id(device["id"])
    asset = await assets.get_asset_by_id(linked["asset_id"])
    assert ticket["company_id"] == asset["company_id"] == 2
    assert ticket["status"] == "open" and ticket["priority"] == "normal"
    assert hostname[:40] in ticket["subject"] and len(ticket["subject"]) <= 255
    assert hostname in ticket["description"] and "SN-1" in ticket["description"]
    association = await db.fetch_one("SELECT * FROM ticket_assets WHERE ticket_id=?", (ticket["id"],))
    assert association["asset_id"] == linked["asset_id"]
    assert linked["setup_ticket_processed"] == 1
    assert create.call_args.kwargs["trigger_automations"] is True


@pytest.mark.anyio
async def test_enrollment_creates_setup_ticket_but_reenrollment_does_not(setup_ticket_db, monkeypatch):
    db, create = setup_ticket_db
    await test_new_enrollment_returns_provisioned_asset(db, monkeypatch)
    create.assert_awaited_once()
    assert len(await db.fetch_all("SELECT * FROM ticket_assets")) == 1


@pytest.mark.anyio
async def test_disabled_company_does_not_backfill_after_enabling(setup_ticket_db):
    db, create = setup_ticket_db
    await db.execute("UPDATE companies SET tray_setup_tickets_enabled=0 WHERE id=1")
    device = await add_device(db, uid="opted-out")
    await service.ensure_device_setup_ticket(device)
    assert (await tray.get_device_by_id(device["id"]))["setup_ticket_processed"] == 1
    await db.execute("UPDATE companies SET tray_setup_tickets_enabled=1 WHERE id=1")
    await service.ensure_device_setup_ticket(await tray.get_device_by_id(device["id"]))
    create.assert_not_awaited()
    await service.ensure_device_setup_ticket(await add_device(db, uid="enabled-later"))
    create.assert_awaited_once()


@pytest.mark.anyio
async def test_preexisting_devices_do_not_create_setup_tickets(setup_ticket_db):
    db, create = setup_ticket_db
    await service.ensure_device_setup_ticket(await tray.get_device_by_uid("existing-device"))
    create.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [{"company": None}, {"status": "revoked"}])
async def test_setup_tickets_skip_unassigned_and_revoked_devices(setup_ticket_db, kwargs):
    db, create = setup_ticket_db
    await service.ensure_device_setup_ticket(await add_device(db, uid="skip-ticket", **kwargs))
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_partial_ticket_failure_reuses_closed_ticket_on_retry(setup_ticket_db, monkeypatch):
    from app.repositories import tickets
    db, create = setup_ticket_db
    device = await add_device(db, uid="retry-link")
    original = tickets.replace_ticket_assets
    async def fail_link(*args):
        raise RuntimeError("connection interrupted")
    monkeypatch.setattr(tickets, "replace_ticket_assets", fail_link)
    await service.ensure_device_setup_ticket(device)
    assert (await tray.get_device_by_id(device["id"]))["setup_ticket_processed"] == 0
    await db.execute("UPDATE tickets SET status='closed'")
    monkeypatch.setattr(tickets, "replace_ticket_assets", original)
    await service.ensure_device_setup_ticket(device)
    create.assert_awaited_once()
    assert (await tray.get_device_by_id(device["id"]))["setup_ticket_processed"] == 1
    assert len(await db.fetch_all("SELECT * FROM ticket_assets")) == 1


@pytest.mark.anyio
async def test_ticket_creation_failure_retries_on_heartbeat(setup_ticket_db):
    from app.api.routes import tray as routes
    from app.schemas.tray import TrayHeartbeatRequest
    from starlette.requests import Request
    db, create = setup_ticket_db
    device = await add_device(db, uid="retry-create")
    normal_create = create.side_effect
    create.side_effect = RuntimeError("ticket service unavailable")
    await service.ensure_device_setup_ticket(device)
    assert (await tray.get_device_by_id(device["id"]))["setup_ticket_processed"] == 0
    create.side_effect = normal_create
    # This fixture only includes asset/link columns, so isolate the liveness
    # write while exercising the actual HTTP heartbeat retry path.
    from unittest.mock import AsyncMock
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setattr(tray, "update_device_heartbeat", AsyncMock())
        response = await routes.heartbeat(TrayHeartbeatRequest(), Request({"type": "http"}), device)
    assert response.status_code == 200
    assert len(await db.fetch_all("SELECT * FROM tickets")) == 1
    assert (await tray.get_device_by_id(device["id"]))["setup_ticket_processed"] == 1


@pytest.mark.anyio
async def test_websocket_checkin_retries_pending_setup_ticket(setup_ticket_db, monkeypatch):
    from unittest.mock import AsyncMock
    db, create = setup_ticket_db
    device = await add_device(db, uid="websocket-retry")
    monkeypatch.setattr(tray, "touch_device_websocket", AsyncMock())
    await service.mark_websocket_alive(device)
    # A second WebSocket worker can have an older device dictionary.
    stale = dict(device, setup_ticket_processed=0)
    await service.mark_websocket_alive(stale)
    create.assert_awaited_once()
    assert stale["setup_ticket_processed"] == 1


@pytest.mark.anyio
async def test_enrol_returns_credentials_while_slow_asset_work_continues(asset_db, monkeypatch):
    from app.api.routes import tray as routes
    from app.schemas.tray import TrayEnrolRequest

    async def token_lookup(token_hash):
        return {"id": 1, "company_id": 1}

    async def create_device(**kwargs):
        return await add_device(asset_db, uid=kwargs["device_uid"])

    async def used(token_id):
        pass

    monkeypatch.setattr(tray, "get_install_token_by_hash", token_lookup)
    monkeypatch.setattr(tray, "create_device", create_device)
    monkeypatch.setattr(tray, "mark_install_token_used", used)
    monkeypatch.setattr(service, "PROVISION_CHECKIN_WAIT_SECONDS", 0.01)
    release = asyncio.Event()
    original = service.ensure_device_asset
    calls = 0

    async def slow_asset(device):
        nonlocal calls
        calls += 1
        await release.wait()
        return await original(device)

    monkeypatch.setattr(service, "ensure_device_asset", slow_asset)
    response = await asyncio.wait_for(routes.enrol_device(
        TrayEnrolRequest(install_token="a" * 32, device_uid="slow-mac", os="darwin"),
        SimpleNamespace(),
    ), timeout=1)
    assert response.auth_token
    assert response.asset_id is None
    device = await tray.get_device_by_uid(response.device_uid)
    task = service._device_provision_tasks[device["id"]]
    try:
        # An authenticated check-in shares the in-flight provisioning attempt.
        assert await service.provision_device_for_checkin(dict(device)) is None
        assert calls == 1
        assert not task.cancelled()
        release.set()
        asset_id = await task
        assert asset_id
        assert (await tray.get_device_by_uid(response.device_uid))["asset_id"] == asset_id
    finally:
        release.set()
        await task
    assert device["id"] not in service._device_provision_tasks


@pytest.mark.anyio
async def test_provision_failure_does_not_block_authentication_or_heartbeat(asset_db, monkeypatch):
    from app.api.dependencies.auth import get_current_tray_device
    from app.api.routes import tray as routes
    from app.schemas.tray import TrayHeartbeatRequest

    device = await add_device(asset_db)
    original = service.ensure_device_asset

    async def fail_asset(device):
        raise RuntimeError("asset database unavailable")

    async def lookup(token_hash):
        return dict(device)

    heartbeats = []

    async def heartbeat(device_id, **kwargs):
        heartbeats.append(device_id)

    monkeypatch.setattr(service, "ensure_device_asset", fail_asset)
    monkeypatch.setattr(tray, "get_device_by_auth_hash", lookup)
    monkeypatch.setattr(tray, "update_device_heartbeat", heartbeat)
    request = SimpleNamespace(headers={"Authorization": "Bearer device-token"}, client=None)
    authenticated = await get_current_tray_device(request)
    response = await routes.heartbeat(TrayHeartbeatRequest(), request, authenticated)
    assert response.status_code == 200
    assert heartbeats == [device["id"]]
    # A subsequent check-in retries and repairs provisioning after recovery.
    monkeypatch.setattr(service, "ensure_device_asset", original)
    assert await service.provision_device_for_checkin(device)


@pytest.mark.anyio
async def test_stalled_provision_attempt_times_out_and_retries(asset_db, monkeypatch):
    device = await add_device(asset_db)
    original = service.ensure_device_asset
    monkeypatch.setattr(service, "PROVISION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(service, "PROVISION_CHECKIN_WAIT_SECONDS", 1)

    async def stalled_asset(device):
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "ensure_device_asset", stalled_asset)
    assert await service.provision_device_for_checkin(device) is None
    assert device["id"] not in service._device_provision_tasks
    monkeypatch.setattr(service, "ensure_device_asset", original)
    monkeypatch.setattr(service, "PROVISION_TIMEOUT_SECONDS", 120)
    assert await service.provision_device_for_checkin(device)


@pytest.mark.anyio
async def test_enrolment_checks_the_reported_version_for_an_upgrade(asset_db, monkeypatch):
    from app.api.routes import tray as routes
    from app.schemas.tray import TrayEnrolRequest
    checked = []
    async def token_lookup(token_hash):
        return {"id": 1, "company_id": 1}
    async def create_device(**kwargs):
        return await add_device(asset_db, uid=kwargs["device_uid"], hostname=kwargs["hostname"])
    async def used(token_id):
        pass
    async def upgrade_new_device(device):
        checked.append(device.get("agent_version"))
        raise RuntimeError("release lookup failed")
    monkeypatch.setattr(tray, "get_install_token_by_hash", token_lookup)
    monkeypatch.setattr(tray, "create_device", create_device)
    monkeypatch.setattr(tray, "mark_install_token_used", used)
    monkeypatch.setattr(routes.rmm_agent_deploy, "upgrade_new_device", upgrade_new_device)
    response = await routes.enrol_device(
        TrayEnrolRequest(
            install_token="a" * 32, device_uid="tray-old", os="windows", hostname="OLD-PC", agent_version="1.2.0",
        ),
        SimpleNamespace(),
    )
    # A failed check never blocks enrolment.
    assert response.auth_token and checked == ["1.2.0"]
