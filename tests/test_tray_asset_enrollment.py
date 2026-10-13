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
