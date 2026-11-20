"""Without Tactical RMM, the Assets list Last Sync shows the tray agent's last check-in."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.main as main_module
from app.features.assets import routes as assets_routes
from app.features.tags import routes as tags_routes
from app.services import modules as modules_service

TRMM_SYNC = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
ASSET = {"id": 10, "company_id": 11, "name": "PC-01", "status": "active", "last_sync": TRMM_SYNC}
TRAY_DEVICE = {
    "device_uid": "uid-1",
    "hostname": "PC-01",
    "last_seen_utc": datetime(2026, 11, 18, 9, 0, 0),
    "ws_last_seen_utc": "2026-11-19 08:30:00",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _wire(monkeypatch, *, trmm_enabled: bool, tray_device: dict | None, rmm_inventory: dict | None = None):
    user = {"id": 1, "is_super_admin": True}
    membership = {"menu_permissions": {"menu.assets": "write"}}
    captured: dict = {}

    async def fake_render(template, request, current_user, *, extra=None):
        captured.update(extra)
        return extra

    monkeypatch.setattr(main_module, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main_module, "_get_effective_company_membership", AsyncMock(return_value=membership))
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", AsyncMock(return_value=True))
    monkeypatch.setattr(main_module, "_feature_pack_available", lambda slug: False)
    monkeypatch.setattr(main_module, "_render_template", fake_render)
    monkeypatch.setattr(assets_routes, "_load_asset_context",
                        AsyncMock(return_value=(user, membership, {"id": 11}, 11, None)))
    monkeypatch.setattr(assets_routes, "_end_user_hidden_columns", AsyncMock(return_value=set()))
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", AsyncMock(return_value=[dict(ASSET)]))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(assets_routes.tray_repo, "list_active_devices_by_asset_ids",
                        AsyncMock(return_value={10: dict(tray_device)} if tray_device else {}))
    monkeypatch.setattr(tags_routes, "can_edit_tags", AsyncMock(return_value=False))
    from app.repositories import rmm_inventory as rmm_inventory_repo

    monkeypatch.setattr(rmm_inventory_repo, "list_for_company_assets",
                        AsyncMock(return_value={10: dict(rmm_inventory)} if rmm_inventory else {}))
    monkeypatch.setattr(modules_service, "get_module",
                        AsyncMock(return_value={"slug": "tacticalrmm", "enabled": trmm_enabled}))
    return captured


@pytest.mark.anyio
async def test_last_sync_uses_latest_tray_check_in_when_trmm_disabled(monkeypatch):
    captured = _wire(monkeypatch, trmm_enabled=False, tray_device=TRAY_DEVICE)

    await assets_routes.assets_page(SimpleNamespace(state=SimpleNamespace()))

    asset = captured["assets"][0]
    assert asset["last_sync_iso"] == "2026-11-19T08:30:00+00:00"
    assert asset["last_sync_sort"] == asset["last_sync_iso"]


@pytest.mark.anyio
async def test_last_sync_keeps_trmm_value_when_trmm_enabled(monkeypatch):
    captured = _wire(monkeypatch, trmm_enabled=True, tray_device=TRAY_DEVICE)

    await assets_routes.assets_page(SimpleNamespace(state=SimpleNamespace()))

    assert captured["assets"][0]["last_sync_iso"] == TRMM_SYNC.isoformat()


@pytest.mark.anyio
async def test_last_sync_unchanged_without_a_tray_agent(monkeypatch):
    captured = _wire(monkeypatch, trmm_enabled=False, tray_device=None)

    await assets_routes.assets_page(SimpleNamespace(state=SimpleNamespace()))

    assert captured["assets"][0]["last_sync_iso"] == TRMM_SYNC.isoformat()
