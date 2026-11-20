"""Shared tray/RMM overlay used wherever assets are shown (tickets, reports, AI search)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import asset_presence


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _modules(monkeypatch, **enabled):
    async def fake(slug):
        return enabled.get(slug, False)

    monkeypatch.setattr(asset_presence, "module_enabled", fake)


def _tray(monkeypatch, devices):
    lookup = AsyncMock(return_value=devices)
    monkeypatch.setattr(asset_presence.tray_repo, "list_active_devices_by_asset_ids", lookup)
    return lookup


@pytest.mark.anyio
async def test_overlays_tray_values_when_tactical_rmm_is_off(monkeypatch):
    _modules(monkeypatch)
    seen = datetime.now(timezone.utc) - timedelta(seconds=20)
    _tray(monkeypatch, {1: {"last_seen_utc": seen, "console_user": "CORP\\alice"},
                        2: {"last_seen_utc": seen - timedelta(days=3)}})
    assets = [
        {"asset_id": 1, "status": "overdue", "last_user": "bob", "last_sync": None},
        {"asset_id": 2, "status": "overdue"},
        {"asset_id": 3, "status": "overdue"},
        {"asset_id": 4, "status": "In repair", "provenance": "manual"},
    ]

    await asset_presence.apply_tray_presence(assets, id_key="asset_id")

    assert assets[0] == {"asset_id": 1, "status": "online", "last_user": "CORP\\alice", "last_sync": seen}
    assert assets[1]["status"] == "offline"
    assert assets[2]["status"] is None
    assert assets[3]["status"] == "In repair"


@pytest.mark.anyio
async def test_keeps_syncro_status_without_tray(monkeypatch):
    _modules(monkeypatch, syncro=True)
    _tray(monkeypatch, {})
    assets = [{"id": 3, "status": "Active"}]

    await asset_presence.apply_tray_presence(assets)

    assert assets[0]["status"] == "Active"


@pytest.mark.anyio
async def test_does_nothing_while_tactical_rmm_is_on(monkeypatch):
    _modules(monkeypatch, tacticalrmm=True)
    lookup = _tray(monkeypatch, {1: {"last_seen_utc": datetime.now(timezone.utc)}})
    assets = [{"id": 1, "status": "overdue", "last_user": "bob"}]

    await asset_presence.apply_tray_presence(assets)

    assert assets == [{"id": 1, "status": "overdue", "last_user": "bob"}]
    lookup.assert_not_awaited()


@pytest.mark.anyio
async def test_only_requested_fields_change(monkeypatch):
    _modules(monkeypatch)
    _tray(monkeypatch, {1: {"last_seen_utc": datetime.now(timezone.utc), "console_user": "alice"}})
    assets = [{"id": 1, "status": "overdue", "last_user": "bob"}]

    await asset_presence.apply_tray_presence(assets, fields=("last_user",))

    assert assets == [{"id": 1, "status": "overdue", "last_user": "alice"}]


def test_rmm_inventory_fields_map_to_asset_columns():
    fields = asset_presence.rmm_inventory_fields({
        "os_name": " Microsoft Windows 11 Pro ", "cpu_model": "Intel Core i5", "ram_bytes": 8 * 1024 ** 3,
        "disk_total_bytes": 256 * 1024 ** 3, "last_boot_at": "2026-11-18 07:00:00",
    })

    assert fields == {
        "os_name": "Microsoft Windows 11 Pro", "cpu_name": "Intel Core i5", "ram_gb": 8.0,
        "hdd_size": "256 GB", "boot_time": datetime(2026, 11, 18, 7, tzinfo=timezone.utc),
    }
    assert asset_presence.rmm_inventory_fields({"ram_bytes": None, "cpu_model": " "}) == {}


@pytest.mark.anyio
async def test_assets_report_uses_tray_values_without_tactical_rmm(monkeypatch):
    from app.services import reports

    _modules(monkeypatch)
    _tray(monkeypatch, {1: {"last_seen_utc": datetime(2026, 11, 19, 8, tzinfo=timezone.utc),
                            "console_user": "alice"}})
    monkeypatch.setattr(reports.assets_repo, "list_company_assets", AsyncMock(return_value=[
        {"id": 1, "name": "PC-01", "status": "overdue", "last_user": "bob",
         "last_sync": datetime(2026, 1, 1)},
    ]))

    detail = await reports._build_assets_detail(5)

    row = detail["assets"][0]
    assert (row["status"], row["last_user"], row["last_sync"]) == ("offline", "alice", "2026-11-19")
