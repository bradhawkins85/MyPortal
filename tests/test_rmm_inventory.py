"""RMM device inventory: agent snapshots, the asset page section and device filters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.database import Database
from app.repositories import rmm_inventory as inventory_repo
from app.services import rmm_inventory as inventory
from tests.test_rmm_scripts import _enrol, _environment, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3


@pytest.fixture
async def inventory_db(sqlite_db, monkeypatch):
    sql = (ROOT / "migrations" / "477_rmm_device_inventory.sql").read_text()
    await sqlite_db.conn.executescript(Database()._adapt_sql_for_sqlite(sql))

    async def execute_many(sql, rows):
        await sqlite_db.conn.executemany(sqlite_db._sql(sql), list(rows))

    sqlite_db.execute_many = execute_many
    monkeypatch.setattr(inventory_repo, "db", sqlite_db)
    yield sqlite_db


def _snapshot(**overrides):
    snapshot = {
        "collected_at": "2026-10-12T01:00:00.123456789Z",
        "hostname": "PC-01", "manufacturer": "Dell Inc.", "model": "Latitude 7440", "serial_number": "ABC1234",
        "os_name": "Microsoft Windows 11 Pro", "os_version": "23H2", "os_build": "22631.4317", "arch": "amd64",
        "domain": "corp.example.com", "cpu_model": "Intel Core i7-1365U", "cpu_cores": 10, "cpu_logical_processors": 12,
        "ram_bytes": 16 * GIB - 300 * 1024 ** 2,
        "last_boot_at": (datetime.now(timezone.utc) - timedelta(days=9)).isoformat(),
        "disks": [{"name": "C:", "file_system": "NTFS", "size_bytes": 500 * GIB, "free_bytes": 50 * GIB, "system": True},
                  {"name": "D:", "file_system": "NTFS", "size_bytes": 1000 * GIB, "free_bytes": 900 * GIB}],
        "physical_disks": [{"model": "Samsung SSD 980", "size_bytes": 512 * GIB, "media_type": "SSD", "bus_type": "NVMe"}],
        "memory_modules": [{"slot": "DIMM A", "size_bytes": 16 * GIB, "speed_mhz": 5200}],
        "gpus": ["Intel Iris Xe"],
        "network_adapters": [{"name": "Wi-Fi", "mac": "aa:bb:cc:dd:ee:ff", "addresses": ["10.0.0.5"]}],
        "software": [
            {"name": "Google Chrome", "version": "129.0.6668.90", "publisher": "Google LLC", "install_date": "2024-10-01"},
            {"name": "Mozilla Firefox (x64 en-US)", "version": "115.3.0", "publisher": "Mozilla"},
            {"name": "google chrome", "version": "129.0.6668.90"},  # duplicate, dropped
            {"name": "  ", "version": "1"},  # no name, dropped
            {"name": "x" * 600, "version": "1", "install_date": "not a date"},
        ],
        "errors": ["gpus: Access denied"],
    }
    snapshot.update(overrides)
    return snapshot


def test_normalise_splits_and_cleans_the_snapshot():
    summary, details, software = inventory.normalise(_snapshot())
    assert summary["disk_total_bytes"] == 1500 * GIB and summary["system_disk_free_bytes"] == 50 * GIB
    assert summary["cpu_cores"] == 10 and summary["os_build"] == "22631.4317"
    assert isinstance(summary["last_boot_at"], datetime) and summary["last_boot_at"].tzinfo is None
    assert [item["name"] for item in software][:2] == ["Google Chrome", "Mozilla Firefox (x64 en-US)"]
    assert len(software) == 3 and len(software[2]["name"]) == 255 and software[2]["install_date"] is None
    assert details["physical_disks"][0]["media_type"] == "SSD" and details["errors"] == ["gpus: Access denied"]


@pytest.mark.anyio
async def test_agent_sends_snapshot_and_refresh_makes_it_due(inventory_db):
    _result, agent = await _enrol(inventory_db)
    assert await inventory.agent_config(agent) == {"due": True, "poll_seconds": inventory.POLL_SECONDS}
    assert await inventory.ingest(agent, _snapshot()) == {"ok": True, "software": 3}
    assert (await inventory.agent_config(agent))["due"] is False

    stored = await inventory_repo.get_for_agent(agent["id"])
    assert stored["asset_id"] == 10 and stored["company_id"] == 1 and stored["software_count"] == 3
    assert stored["details"]["disks"][0]["name"] == "C:"
    # A second snapshot replaces the software list rather than adding to it.
    await inventory.ingest(agent, _snapshot(software=[{"name": "7-Zip", "version": "23.01"}]))
    assert [item["name"] for item in await inventory_repo.list_software(agent["id"])] == ["7-Zip"]

    await inventory.request_refresh(agent, 9)
    assert (await inventory.agent_config(agent))["due"] is True
    await inventory.ingest(agent, _snapshot())
    assert (await inventory.agent_config(agent))["due"] is False

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert inventory.snapshot_due({"received_at": now - timedelta(hours=25)}, now)
    assert not inventory.snapshot_due({"received_at": now - timedelta(hours=1)}, now)
    assert inventory.snapshot_due({"received_at": now - timedelta(hours=1), "refresh_requested_at": now}, now)


@pytest.mark.anyio
async def test_agent_routes_validate_the_snapshot(inventory_db):
    from app.features.rmm import inventory_routes as routes

    _result, agent = await _enrol(inventory_db)
    payload = routes.AgentInventory(**_snapshot(unknown_field="ignored"))
    assert (await routes.agent_inventory(payload, agent))["software"] == 3
    assert (await routes.agent_inventory_config(agent))["due"] is False
    with pytest.raises(ValidationError):
        routes.AgentInventory(software=[{"name": "x"}] * (inventory.MAX_SOFTWARE + 1))
    with pytest.raises(ValidationError):
        routes.AgentInventory(ram_bytes=-1)


@pytest.mark.anyio
async def test_filter_agents_by_hardware_and_software(inventory_db):
    _r1, big = await _enrol(inventory_db, uid="agent-uid-0001", tray_id=5, asset_id=10)
    _r2, small = await _enrol(inventory_db, uid="agent-uid-0002", tray_id=6, asset_id=11)
    _r3, silent = await _enrol(inventory_db, uid="agent-uid-0003", tray_id=7, asset_id=20)
    await inventory.ingest(big, _snapshot())
    await inventory.ingest(small, _snapshot(ram_bytes=8 * GIB, os_name="Ubuntu 24.04 LTS", os_version="24.04",
                                            os_build="6.8.0-45-generic",
                                            software=[{"name": "Google Chrome", "version": "118.0.1"}]))
    agents = [big, small, silent]

    async def ids(*conditions):
        return [agent["id"] for agent in await inventory.filter_agents(agents, list(conditions))]

    assert await ids() == [big["id"], small["id"], silent["id"]]
    # 16 GB reports as a little less; it still counts as 16.
    assert await ids({"field": "ram_gb", "operator": "at_least", "value": "16"}) == [big["id"]]
    assert await ids({"field": "ram_gb", "operator": "below", "value": "16"}) == [small["id"]]
    assert await ids({"field": "software", "operator": "installed", "value": "firefox"}) == [big["id"]]
    # A device without a snapshot matches no condition, not even "is not installed".
    assert await ids({"field": "software", "operator": "not_installed", "value": "Firefox"}) == [small["id"]]
    assert await ids({"field": "software", "operator": "installed", "value": "Mozilla*x64*"}) == [big["id"]]
    assert await ids({"field": "software", "operator": "version_below", "value": "Google Chrome 120"}) == [small["id"]]
    assert await ids({"field": "software", "operator": "version_at_least", "value": "Google Chrome 120"}) == [big["id"]]
    assert await ids({"field": "os_name", "operator": "contains", "value": "windows"},
                     {"field": "system_disk_free_gb", "operator": "at_least", "value": "40"}) == [big["id"]]
    # Windows 11 is build 22000 and later.
    assert await ids({"field": "os_build", "operator": "at_least", "value": "22000"}) == [big["id"]]
    assert await ids({"field": "os_name", "operator": "starts_with", "value": "ubuntu"},
                     {"field": "os_version", "operator": "at_least", "value": "22.04"}) == [small["id"]]
    assert await ids({"field": "uptime_days", "operator": "above", "value": "7"}) == [big["id"], small["id"]]
    assert await ids({"field": "ram_gb", "operator": "contains", "value": "16"}) == []  # wrong operator


def test_parse_and_describe_conditions():
    parsed = inventory.parse_conditions(
        ["ram_gb", "software", "os_name", "software"],
        ["at_least", "not_installed", "contains", "version_below"],
        [" 16 ", "Google Chrome", "", "Google Chrome 120.1"],
    )
    assert parsed == [
        {"field": "ram_gb", "operator": "at_least", "value": "16"},
        {"field": "software", "operator": "not_installed", "value": "Google Chrome"},
        {"field": "software", "operator": "version_below", "value": "Google Chrome 120.1"},
    ]
    assert [inventory.describe_condition(item) for item in parsed] == [
        "RAM (GB) is at least 16", "Google Chrome is not installed", "Google Chrome is installed below version 120.1",
    ]
    with pytest.raises(inventory.InventoryConditionError) as exc:
        inventory.parse_conditions(["ram_gb", "bogus", "software", "ram_gb"],
                                   ["contains", "is", "version_below", "at_least"], ["16", "x", "Chrome", "lots"])
    assert set(exc.value.errors) == {"inventory_1", "inventory_2", "inventory_3", "inventory_4"}


def test_reads_go_times():
    # Go sends up to nine fraction digits, which Python 3.10's fromisoformat refuses.
    assert inventory._as_datetime("2026-10-12T01:00:00.123456789Z") == datetime(2026, 10, 12, 1, 0, 0, 123456)
    assert inventory._as_datetime("2026-10-12T11:00:00.5+10:00") == datetime(2026, 10, 12, 1, 0, 0, 500000)
    assert inventory._as_datetime("2026-10-12T01:00:00Z") == datetime(2026, 10, 12, 1, 0)
    assert inventory._as_datetime("2026-13-12T01:00:00Z") is None and inventory._as_datetime("junk") is None


def test_format_bytes():
    assert inventory.format_bytes(16 * GIB - 300 * 1024 ** 2) == "15.7 GB"
    assert inventory.format_bytes(500 * GIB) == "500 GB"
    assert inventory.format_bytes(2 * 1024 ** 4) == "2.0 TB"
    assert inventory.format_bytes(None) == "" and inventory.format_bytes(512) == "512 B"


@pytest.mark.anyio
async def test_asset_section_renders_snapshot_and_refresh(inventory_db):
    from app.features.rmm import inventory_routes as routes

    _result, agent = await _enrol(inventory_db)
    assert (await routes.asset_inventory_context(1, 11, can_refresh=True)) is None  # no agent on that asset
    empty = await routes.asset_inventory_context(1, 10, can_refresh=True)
    assert empty["inventory"] is None and not empty["refresh_pending"]
    await inventory.request_refresh(agent, 9)
    assert (await routes.asset_inventory_context(1, 10, can_refresh=True))["refresh_pending"]
    await inventory.ingest(agent, _snapshot(software=[*_snapshot()["software"], {"name": "<script>x</script>"}]))
    context = await routes.asset_inventory_context(1, 10, can_refresh=True)
    assert not context["refresh_pending"] and context["inventory"]["model"] == "Latitude 7440"
    # Another company's asset is not shown.
    assert (await routes.asset_inventory_context(2, 10, can_refresh=True)) is None

    env = _environment()
    html = env.get_template("rmm/_asset_inventory.html").render(
        rmm={"inventory": context}, asset={"id": 10}, csrf_token="t",
    )
    assert 'action="/rmm/inventory/assets/10/refresh"' in html and "Refresh now" in html
    assert "Latitude 7440" in html and "15.7 GB" in html and "Samsung SSD 980" in html
    assert "Google Chrome" in html and "&lt;script&gt;" in html and "<script>x" not in html
    assert "Some details could not be read" in html
    read_only = env.get_template("rmm/_asset_inventory.html").render(
        rmm={"inventory": {**empty, "can_refresh": False}}, asset={"id": 10}, csrf_token="t",
    )
    assert "Refresh now" not in read_only and "has not sent a snapshot yet" in read_only
