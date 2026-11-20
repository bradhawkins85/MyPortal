"""With Tactical RMM disabled, asset status comes from the tray agent and TRMM/Syncro details are hidden."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.features.assets import routes as assets_routes
from tests.test_asset_last_sync_tray import _wire
from tests.test_asset_logged_on_user import _patch_detail, _request

DETAIL_TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "assets" / "detail.html"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _online_device() -> dict:
    return {"device_uid": "uid-1", "last_seen_utc": datetime.now(timezone.utc) - timedelta(seconds=30)}


def _offline_device() -> dict:
    return {"device_uid": "uid-1", "last_seen_utc": datetime.now(timezone.utc) - timedelta(days=2)}


def _modules(monkeypatch, **enabled: bool) -> None:
    async def fake_enabled(slug: str) -> bool:
        return enabled.get(slug, False)

    monkeypatch.setattr(assets_routes, "_module_enabled", fake_enabled)


async def _listed_status(monkeypatch, *, tray_device, provenance="integration", **modules):
    monkeypatch.setattr("tests.test_asset_last_sync_tray.ASSET",
                        {"id": 10, "company_id": 11, "name": "PC-01", "status": "overdue",
                         "provenance": provenance})
    captured = _wire(monkeypatch, trmm_enabled=False, tray_device=tray_device)
    _modules(monkeypatch, **modules)
    await assets_routes.assets_page(SimpleNamespace(state=SimpleNamespace()))
    return captured["assets"][0]["status"]


@pytest.mark.anyio
async def test_list_status_is_online_from_tray_when_trmm_disabled(monkeypatch):
    assert await _listed_status(monkeypatch, tray_device=_online_device()) == "online"


@pytest.mark.anyio
async def test_list_status_is_offline_from_stale_tray(monkeypatch):
    assert await _listed_status(monkeypatch, tray_device=_offline_device()) == "offline"


@pytest.mark.anyio
async def test_list_drops_stale_trmm_status_without_tray(monkeypatch):
    assert await _listed_status(monkeypatch, tray_device=None) is None


@pytest.mark.anyio
async def test_list_keeps_syncro_status_without_tray(monkeypatch):
    assert await _listed_status(monkeypatch, tray_device=None, syncro=True) == "overdue"


@pytest.mark.anyio
async def test_list_keeps_trmm_status_when_trmm_enabled(monkeypatch):
    assert await _listed_status(monkeypatch, tray_device=_online_device(), tacticalrmm=True) == "overdue"


@pytest.mark.anyio
async def test_list_keeps_manual_status(monkeypatch):
    assert await _listed_status(monkeypatch, tray_device=_online_device(), provenance="manual") == "overdue"


SOURCES = [
    {"id": 1, "source": "syncro", "external_id": "s-1", "owned_fields": []},
    {"id": 2, "source": "tacticalrmm", "external_id": "t-1", "owned_fields": []},
]


async def _detail(monkeypatch, *, tray_devices, **modules):
    renderer, _ = _patch_detail(
        monkeypatch,
        record={"id": 10, "company_id": 3, "status": "overdue", "provenance": "integration"},
        tray_devices=tray_devices,
    )
    monkeypatch.setattr(assets_routes.asset_repo, "list_asset_sources", AsyncMock(return_value=list(SOURCES)))
    _modules(monkeypatch, **modules)
    await assets_routes.asset_detail_page(_request(), 10, customer_preview=False)
    return renderer.await_args.kwargs["extra"]


@pytest.mark.anyio
async def test_detail_hides_disabled_sources_and_uses_tray_status(monkeypatch):
    extra = await _detail(monkeypatch, tray_devices={10: _online_device()}, syncro=True)

    assert extra["asset"]["status"] == "online"
    assert [source["source"] for source in extra["asset_sources"]] == ["syncro"]
    assert extra["integration_enabled"] == {"syncro": True, "tacticalrmm": False}


@pytest.mark.anyio
async def test_detail_keeps_everything_when_modules_enabled(monkeypatch):
    extra = await _detail(monkeypatch, tray_devices={10: _online_device()}, syncro=True, tacticalrmm=True)

    assert extra["asset"]["status"] == "overdue"
    assert len(extra["asset_sources"]) == 2


def test_inventory_ids_are_gated_by_module():
    template = DETAIL_TEMPLATE.read_text(encoding="utf-8")
    assert "{% if integration_enabled.syncro %}<dt>Syncro ID</dt>" in template
    assert "{% if integration_enabled.tacticalrmm %}<dt>Tactical ID</dt>" in template


INVENTORY = {"os_name": "Microsoft Windows 11 Pro", "cpu_model": "Intel Core i7-1365U",
             "ram_bytes": 16 * 1024 ** 3, "disk_total_bytes": 512 * 1024 ** 3,
             "last_boot_at": "2026-11-18 07:00:00"}


async def _listed_inventory(monkeypatch, *, provenance="integration", **modules):
    monkeypatch.setattr("tests.test_asset_last_sync_tray.ASSET",
                        {"id": 10, "company_id": 11, "name": "PC-01", "provenance": provenance,
                         "os_name": "Windows 10", "cpu_name": "Old CPU", "ram_gb": 8, "hdd_size": "256 GB"})
    captured = _wire(monkeypatch, trmm_enabled=False, tray_device=None, rmm_inventory=INVENTORY)
    _modules(monkeypatch, **modules)
    await assets_routes.assets_page(SimpleNamespace(state=SimpleNamespace()))
    return captured["assets"][0]


@pytest.mark.anyio
async def test_list_inventory_comes_from_rmm_agent_when_trmm_disabled(monkeypatch):
    asset = await _listed_inventory(monkeypatch)

    assert asset["os_name"] == "Microsoft Windows 11 Pro"
    assert asset["cpu_name"] == "Intel Core i7-1365U"
    assert asset["ram_gb"] == "16"
    assert asset["hdd_size"] == "512 GB"
    assert asset["boot_time_iso"] == "2026-11-18T07:00:00+00:00"


@pytest.mark.anyio
async def test_list_inventory_keeps_manual_values(monkeypatch):
    asset = await _listed_inventory(monkeypatch, provenance="manual")

    assert asset["os_name"] == "Windows 10"
    assert asset["cpu_name"] == "Old CPU"
