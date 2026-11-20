"""Without Tactical RMM, the Assets list Last user matches the tray agent's logged-in user."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.features.assets import routes as assets_routes
from tests.test_asset_last_sync_tray import TRAY_DEVICE, _wire


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _listed_last_user(monkeypatch, *, trmm_enabled: bool, tray_device: dict | None, last_user: str | None):
    monkeypatch.setattr("tests.test_asset_last_sync_tray.ASSET",
                        {"id": 10, "company_id": 11, "name": "PC-01", "status": "active",
                         "last_user": last_user})
    captured = _wire(monkeypatch, trmm_enabled=trmm_enabled, tray_device=tray_device)
    await assets_routes.assets_page(SimpleNamespace(state=SimpleNamespace()))
    return captured["assets"][0]["last_user"]


@pytest.mark.anyio
async def test_last_user_uses_tray_console_user_when_trmm_disabled(monkeypatch):
    device = {**TRAY_DEVICE, "console_user": "CORP\\alice", "console_user_active": 1}
    assert await _listed_last_user(monkeypatch, trmm_enabled=False, tray_device=device,
                                   last_user="CORP\\bob") == "CORP\\alice"


@pytest.mark.anyio
async def test_last_user_uses_last_tray_user_when_nobody_signed_in(monkeypatch):
    device = {**TRAY_DEVICE, "console_user": "CORP\\alice", "console_user_active": 0}
    assert await _listed_last_user(monkeypatch, trmm_enabled=False, tray_device=device,
                                   last_user="CORP\\bob") == "CORP\\alice"


@pytest.mark.anyio
async def test_last_user_ignores_tray_machine_account(monkeypatch):
    device = {**TRAY_DEVICE, "console_user": "PC-01$"}
    assert await _listed_last_user(monkeypatch, trmm_enabled=False, tray_device=device,
                                   last_user="CORP\\bob") == "CORP\\bob"


@pytest.mark.anyio
async def test_last_user_keeps_trmm_value_when_trmm_enabled(monkeypatch):
    device = {**TRAY_DEVICE, "console_user": "CORP\\alice"}
    assert await _listed_last_user(monkeypatch, trmm_enabled=True, tray_device=device,
                                   last_user="CORP\\bob") == "CORP\\bob"
