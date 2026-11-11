"""Windows blue screen (minidump) analysis: storage, ingest, analysis, routes and the asset page section."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import asset_minidumps as repo
from tests.test_rmm_scripts import _SqliteDb, anyio_backend  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "555_asset_minidumps.sql"
NOW = datetime(2026, 11, 11, 3, 0, 0)


def _migration_sql() -> str:
    return Database()._adapt_sql_for_sqlite((ROOT / "migrations" / MIGRATION).read_text(encoding="utf-8"))


@pytest.fixture
async def store(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT, serial_number TEXT, os_name TEXT,
          archived_at TEXT, tray_device_uid TEXT);
        CREATE TABLE tray_devices (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, device_uid TEXT, os TEXT,
          os_version TEXT, hostname TEXT, serial_number TEXT, agent_version TEXT, status TEXT DEFAULT 'active',
          last_seen_utc TEXT);
        CREATE TABLE tickets (id INTEGER PRIMARY KEY, company_id INT, subject TEXT, status TEXT, requester_id INT);
        CREATE TABLE ticket_assets (ticket_id INT, asset_id INT);
        INSERT INTO companies VALUES (1, 'Contoso'), (2, 'Other');
        INSERT INTO assets (id, company_id, name, serial_number, os_name, tray_device_uid) VALUES
          (10, 1, 'PC-01', 'SN1', 'Windows 11 Pro', NULL), (11, 1, 'PC-02', 'SN2', 'Windows 11 Pro', NULL),
          (12, 1, 'PC-03', 'SN-UID', 'Windows 11 Pro', 'dev-9'),
          (20, 2, 'OTHER', 'SN3', 'Windows 11 Pro', NULL);
        INSERT INTO tray_devices (id, company_id, asset_id, device_uid, os, hostname, serial_number, status) VALUES
          (5, 1, 10, 'dev-1', 'windows', 'pc-01', 'SN1', 'active'),
          (6, 1, NULL, 'dev-2', 'windows', 'pc-02', 'sn2', 'active'),
          (8, 1, NULL, 'dev-8', 'windows', 'nomatch', 'ZZZ', 'active'),
          (9, 1, NULL, 'dev-9', 'windows', 'pc-03', '', 'active'),
          (30, 1, 20, 'dev-30', 'windows', 'cross', 'X', 'active');
    """)
    await conn.executescript(_migration_sql())
    fake = _SqliteDb(conn)
    monkeypatch.setattr(repo, "db", fake)
    yield {"db": fake, "conn": conn}
    await conn.close()


def _device(device_id: int, **overrides) -> dict:
    devices = {
        5: {"id": 5, "company_id": 1, "asset_id": 10, "device_uid": "dev-1", "serial_number": "SN1"},
        6: {"id": 6, "company_id": 1, "asset_id": None, "device_uid": "dev-2", "serial_number": "sn2"},
        8: {"id": 8, "company_id": 1, "asset_id": None, "device_uid": "dev-8", "serial_number": "ZZZ"},
        9: {"id": 9, "company_id": 1, "asset_id": None, "device_uid": "dev-9", "serial_number": ""},
        30: {"id": 30, "company_id": 1, "asset_id": 20, "device_uid": "dev-30", "serial_number": "X"},
    }
    return {**devices[device_id], **overrides}


def _record(name: str = "111126-8765-01.dmp", *, crashed: datetime | None = None, **overrides) -> dict:
    crashed = crashed or NOW - timedelta(hours=1)
    record = {
        "file_name": name, "file_size": 1482304, "file_modified_at": crashed + timedelta(minutes=2),
        "crashed_at": crashed, "bugcheck_code": 0xD1, "bugcheck_name": "DRIVER_IRQL_NOT_LESS_OR_EQUAL",
        "suspect_driver": "rtwlane.sys", "supported": True, "summary": {"file_name": name, "bugcheck_code": 0xD1},
    }
    record.update(overrides)
    return record


async def _add(asset_id: int = 10, company_id: int = 1, device_id: int = 5, **kwargs) -> int:
    record = _record(**kwargs)
    assert await repo.upsert_dump(company_id=company_id, asset_id=asset_id, tray_device_id=device_id, record=record)
    rows = await repo.list_for_asset(asset_id)
    return next(row["id"] for row in rows if row["file_name"] == record["file_name"])


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_resolve_asset_for_device(store):
    assert (await repo.resolve_asset_for_device(_device(5)))["id"] == 10
    by_serial = await repo.resolve_asset_for_device(_device(6))
    assert by_serial == {"id": 11, "company_id": 1, "name": "PC-02"}
    assert (await repo.resolve_asset_for_device(_device(9)))["id"] == 12  # assets.tray_device_uid
    assert await repo.resolve_asset_for_device(_device(8)) is None
    # A device may only report for an asset in its own company.
    assert await repo.resolve_asset_for_device(_device(30)) is None


@pytest.mark.anyio
async def test_resolve_asset_ignores_ambiguous_or_archived_serials(store):
    await store["conn"].execute(
        "INSERT INTO assets (id, company_id, name, serial_number) VALUES (13, 1, 'PC-02-OLD', 'SN2')")
    assert await repo.resolve_asset_for_device(_device(6)) is None
    await store["conn"].execute("UPDATE assets SET archived_at = '2026-01-01' WHERE id = 13")
    assert (await repo.resolve_asset_for_device(_device(6)))["id"] == 11


@pytest.mark.anyio
async def test_upsert_is_idempotent(store):
    record = _record()
    assert await repo.upsert_dump(company_id=1, asset_id=10, tray_device_id=5, record=record) is True
    assert await repo.upsert_dump(company_id=1, asset_id=10, tray_device_id=5, record=record) is False
    rows = await repo.list_for_asset(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["summary"] == record["summary"] and row["analysis"] is None
    assert row["supported"] is True and row["analysis_status"] == "none"
    assert row["bugcheck_code"] == 0xD1 and row["suspect_driver"] == "rtwlane.sys"


@pytest.mark.anyio
async def test_list_is_scoped_and_newest_first(store):
    await _add(name="old.dmp", crashed=NOW - timedelta(days=3))
    await _add(name="new.dmp", crashed=NOW - timedelta(hours=1))
    await _add(asset_id=20, company_id=2, device_id=30, name="other.dmp")
    assert [row["file_name"] for row in await repo.list_for_asset(10)] == ["new.dmp", "old.dmp"]
    assert [row["file_name"] for row in await repo.list_for_asset(20)] == ["other.dmp"]
    assert await repo.count_for_asset(10) == 2


@pytest.mark.anyio
async def test_prune_keeps_newest_and_running(store):
    ids = [await _add(name=f"d{i:02d}.dmp", crashed=NOW - timedelta(days=i + 1)) for i in range(55)]
    oldest = ids[-1]
    assert await repo.claim_analysis(oldest, user_id=1, ticket_id=None, model="m", now=NOW,
                                     stale_before=NOW - timedelta(minutes=15))
    removed = await repo.prune(10, keep=50)
    assert removed == 4
    names = [row["file_name"] for row in await repo.list_for_asset(10)]
    assert len(names) == 51 and names[0] == "d00.dmp" and "d54.dmp" in names and "d50.dmp" not in names


@pytest.mark.anyio
async def test_claim_analysis_is_exclusive(store):
    dump_id = await _add()
    stale = NOW - timedelta(minutes=15)
    assert await repo.claim_analysis(dump_id, user_id=7, ticket_id=3, model="m1", now=NOW, stale_before=stale) is True
    assert await repo.claim_analysis(dump_id, user_id=8, ticket_id=None, model="m2", now=NOW, stale_before=stale) is False
    row = await repo.get_dump(dump_id)
    assert row["analysis_status"] == "analysing" and row["analysed_by_user_id"] == 7 and row["ticket_id"] == 3
    # A run that never finished can be taken over once it is stale.
    later = NOW + timedelta(minutes=20)
    assert await repo.claim_analysis(dump_id, user_id=8, ticket_id=None, model="m2", now=later,
                                     stale_before=later - timedelta(minutes=15)) is True
    assert (await repo.get_dump(dump_id))["ticket_id"] is None


@pytest.mark.anyio
async def test_finish_and_fail_stale(store):
    done = await _add(name="done.dmp")
    stuck = await _add(name="stuck.dmp")
    stale = NOW - timedelta(minutes=15)
    for dump_id in (done, stuck):
        assert await repo.claim_analysis(dump_id, user_id=1, ticket_id=None, model="m", now=NOW - timedelta(minutes=20),
                                         stale_before=stale - timedelta(minutes=20))
    await repo.finish_analysis(done, status="completed", analysis={"likely_cause": "x"}, error=None, now=NOW)
    assert await repo.fail_stale(10, stale, error="The analysis did not finish. Try again.") == 1
    rows = {row["file_name"]: row for row in await repo.list_for_asset(10)}
    assert rows["done.dmp"]["analysis_status"] == "completed" and rows["done.dmp"]["analysis"] == {"likely_cause": "x"}
    assert rows["done.dmp"]["analysed_at"] is not None
    assert rows["stuck.dmp"]["analysis_status"] == "failed"
    assert rows["stuck.dmp"]["analysis_error"] == "The analysis did not finish. Try again."


@pytest.mark.anyio
async def test_migration_is_idempotent(store):
    await store["conn"].executescript(_migration_sql())
    text = (ROOT / "migrations" / MIGRATION).read_text(encoding="utf-8")
    assert text.startswith("-- phase: expand") and "CREATE TABLE IF NOT EXISTS asset_minidumps" in text
    assert "uq_asset_minidumps_file" in text


# --------------------------------------------------------------------------- #
# Stop codes and ingest
# --------------------------------------------------------------------------- #

from app.services import minidump_analysis as service  # noqa: E402
from app.services import minidump_stop_codes as stop_codes  # noqa: E402

CONTRACT = {
    "file_name": "111126-8765-01.dmp",
    "file_size": 1482304,
    "file_modified_at": "2026-11-11T02:03:04Z",
    "supported": True,
    "error": "",
    "architecture": "x64",
    "bugcheck_code": 209,
    "bugcheck_parameters": ["0x0000000000000028", "0x0000000000000002", "0x0000000000000000", "0xfffff80512345678"],
    "crashed_at": "2026-11-11T02:01:00Z",
    "uptime_seconds": 86400,
    "windows_build": 22631,
    "processor_count": 8,
    "dump_type": 4,
    "faulting_address": {"address": "0xfffff80512345678", "driver": "rtwlane.sys", "offset": "0x5678"},
    "parameter_drivers": [
        {"parameter": 4, "address": "0xfffff80512345678", "driver": "rtwlane.sys", "offset": "0x5678"}],
    "stack_drivers": [{"driver": "rtwlane.sys", "offset": "0x5678"}, {"driver": "ntoskrnl.exe", "offset": "0x3f5a10"}],
    "suspect_driver": "rtwlane.sys",
    "drivers": [{"name": "rtwlane.sys", "base": "0xfffff80512340000", "size": 9437184,
                 "timestamp": "2021-03-04T05:06:07Z", "checksum": 123456}],
    "unloaded_drivers": [{"name": "dump_storahc", "start": "0xfffff80600000000", "end": "0xfffff80600020000"}],
}


def _summary(**overrides) -> dict:
    return {**json.loads(json.dumps(CONTRACT)), **overrides}


def test_stop_code_labels():
    assert stop_codes.label(0xD1) == "0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL"
    assert stop_codes.label(0x124) == "0x00000124 WHEA_UNCORRECTABLE_ERROR"
    assert stop_codes.label(0xC000021A) == "0xC000021A WINLOGON_FATAL_ERROR"
    assert stop_codes.label(0xABCD) == "0x0000ABCD"
    assert stop_codes.label(None) == "Unknown"
    assert stop_codes.name(0x7E) == "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED" and stop_codes.name(0xABCD) == ""
    assert len(stop_codes.parameter_meanings(0xD1)) == 4
    assert stop_codes.parameter_meanings(0xABCD) == [] and stop_codes.parameter_meanings(None) == []
    assert len(stop_codes.STOP_CODES) >= 70
    for code, (name, meanings) in stop_codes.STOP_CODES.items():
        assert isinstance(code, int) and name == name.upper() and len(name) <= 96
        assert len(meanings) in (0, 4)


def test_clean_summary_keeps_known_fields_only():
    raw = _summary(extra="x")
    raw["drivers"][0]["evil"] = "<script>"
    raw["faulting_address"]["more"] = 1
    assert service.clean_summary(raw, now=NOW) == CONTRACT


def test_clean_summary_bounds_everything():
    raw = _summary(
        drivers=[{"name": f"drv{i}.sys", "base": "0xfffff80000000000", "size": 4096, "timestamp": "", "checksum": 0}
                 for i in range(2000)],
        stack_drivers=[{"driver": "a" * 5000 + ".sys", "offset": "0x1"} for _ in range(500)],
        bugcheck_parameters=["0x1"] * 9, bugcheck_code="abc", file_size=-1,
        uptime_seconds=10 ** 12, processor_count=-4,
    )
    cleaned = service.clean_summary(raw, now=NOW)
    assert len(cleaned["drivers"]) <= 512 and len(cleaned["stack_drivers"]) == 64
    assert all(len(ref["driver"]) <= 64 for ref in cleaned["stack_drivers"])
    assert len(cleaned["bugcheck_parameters"]) == 4
    assert cleaned["bugcheck_code"] is None and cleaned["file_size"] == 0
    assert cleaned["uptime_seconds"] == 0 and cleaned["processor_count"] == 0
    assert len(json.dumps(cleaned)) <= service.MAX_SUMMARY_BYTES
    assert len(cleaned["drivers"]) > 100  # trimmed only as far as needed


def test_clean_summary_rejects_bad_hex_and_names():
    raw = _summary(file_name="..\\evil.dmp", suspect_driver="<b>x.sys",
                   bugcheck_parameters=["0xZZ", "<script>", "0xFFFF", "12"])
    raw["faulting_address"] = {"address": "0xZZ", "driver": "<b>x.sys", "offset": "<script>"}
    raw["drivers"][0]["name"] = "<b>x.sys"
    cleaned = service.clean_summary(raw, now=NOW)
    assert cleaned["file_name"] == "evil.dmp" and cleaned["suspect_driver"] == "bx.sys"
    assert cleaned["bugcheck_parameters"] == ["", "", "0xffff", ""]
    assert cleaned["faulting_address"] == {"address": "", "driver": "bx.sys", "offset": ""}
    assert cleaned["drivers"][0]["name"] == "bx.sys"
    assert service.clean_summary(_summary(file_name=""), now=NOW) is None
    assert service.clean_summary(_summary(file_modified_at="nonsense"), now=NOW) is None
    assert service.clean_summary("x", now=NOW) is None and service.clean_summary(None, now=NOW) is None
    # Anything that is not a mapping or list where one is expected is dropped, not trusted.
    odd = service.clean_summary(_summary(drivers="x", stack_drivers={"a": 1}, faulting_address="y"), now=NOW)
    assert odd["drivers"] == [] and odd["stack_drivers"] == [] and odd["faulting_address"] is None


def test_clean_summary_fixes_implausible_times():
    for bad in ("1601-01-01T00:00:00Z", "", "2040-01-01T00:00:00Z", "nonsense", 5):
        assert service.clean_summary(_summary(crashed_at=bad), now=NOW)["crashed_at"] == ""
    ahead = service.clean_summary(_summary(crashed_at="2026-11-11T05:00:00Z"), now=NOW)
    assert ahead["crashed_at"] == "2026-11-11T05:00:00Z"
    offset = service.clean_summary(_summary(crashed_at="2026-11-11T12:01:00+10:00"), now=NOW)
    assert offset["crashed_at"] == "2026-11-11T02:01:00Z"


@pytest.mark.anyio
async def test_ingest_stores_names_and_dedupes(store):
    dumps = [_summary(), _summary(file_name="111026-1111-01.dmp", bugcheck_code=0x124, suspect_driver="")]
    assert await service.ingest(_device(5), dumps) == {"received": 2, "stored": 2, "duplicates": 0, "rejected": 0}
    rows = {row["file_name"]: row for row in await repo.list_for_asset(10)}
    first = rows["111126-8765-01.dmp"]
    assert first["company_id"] == 1 and first["asset_id"] == 10 and first["tray_device_id"] == 5
    assert first["bugcheck_name"] == "DRIVER_IRQL_NOT_LESS_OR_EQUAL" and first["suspect_driver"] == "rtwlane.sys"
    assert first["summary"] == CONTRACT and str(first["crashed_at"]).startswith("2026-11-11 02:01:00")
    assert rows["111026-1111-01.dmp"]["bugcheck_name"] == "WHEA_UNCORRECTABLE_ERROR"
    assert rows["111026-1111-01.dmp"]["suspect_driver"] is None
    again = await service.ingest(_device(5), dumps)
    assert again == {"received": 2, "stored": 0, "duplicates": 2, "rejected": 0}
    assert await repo.count_for_asset(10) == 2


@pytest.mark.anyio
async def test_ingest_uses_file_time_when_crash_time_missing(store):
    await service.ingest(_device(5), [_summary(crashed_at="")])
    row = (await repo.list_for_asset(10))[0]
    assert str(row["crashed_at"]).startswith("2026-11-11 02:03:04")


@pytest.mark.anyio
async def test_ingest_ignores_device_without_asset(store):
    counts = await service.ingest(_device(8), [_summary()])
    assert counts == {"received": 1, "stored": 0, "duplicates": 0, "rejected": 0}
    cursor = await store["conn"].execute("SELECT COUNT(*) FROM asset_minidumps")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.anyio
async def test_ingest_lands_on_asset_matched_by_serial(store):
    assert (await service.ingest(_device(6), [_summary()]))["stored"] == 1
    assert await repo.count_for_asset(11) == 1


@pytest.mark.anyio
async def test_ingest_rejects_non_list_and_caps_count(store):
    assert (await service.ingest(_device(5), "x"))["received"] == 0
    assert (await service.ingest(_device(5), None))["received"] == 0
    dumps = [_summary(file_name=f"d{i:02d}.dmp") for i in range(30)]
    dumps[3] = "junk"
    counts = await service.ingest(_device(5), dumps)
    assert counts == {"received": 20, "stored": 19, "duplicates": 0, "rejected": 1}


@pytest.mark.anyio
async def test_ingest_prunes(store):
    for batch in range(3):
        dumps = [
            _summary(file_name=f"d{batch}-{i:02d}.dmp",
                     crashed_at=(NOW - timedelta(days=batch * 20 + i + 1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for i in range(20 if batch < 2 else 15)
        ]
        await service.ingest(_device(5), dumps)
    assert await repo.count_for_asset(10) == 50
