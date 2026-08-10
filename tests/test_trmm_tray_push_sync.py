"""Tests for immediate agent-side Tactical RMM tray synchronisation."""

import asyncio
from unittest.mock import AsyncMock

from app.api.routes import tray as tray_routes
from app.schemas.tray import TrayTRMMSyncRequest
from app.services import asset_importer, tacticalrmm


def test_sync_tactical_agent_imports_and_links_one_agent(monkeypatch):
    async def run():
        monkeypatch.setattr(
            asset_importer.company_repo,
            "get_company_by_id",
            AsyncMock(return_value={"id": 7, "tacticalrmm_client_id": "client-7"}),
        )
        monkeypatch.setattr(
            tacticalrmm,
            "fetch_agent",
            AsyncMock(return_value={"agent_id": "agent-1", "hostname": "PC-1"}),
        )
        upsert = AsyncMock(return_value=42)
        monkeypatch.setattr(asset_importer.assets_repo, "upsert_asset", upsert)
        monkeypatch.setattr(
            asset_importer.tray_repo,
            "get_device_by_uid",
            AsyncMock(return_value={"id": 9, "company_id": 7}),
        )
        link = AsyncMock()
        monkeypatch.setattr(asset_importer.tray_repo, "link_device_to_asset", link)
        monkeypatch.setattr(
            asset_importer, "_sync_tactical_asset_custom_fields", AsyncMock()
        )

        result = await asset_importer.sync_tactical_agent(
            7, agent_id="agent-1", tray_device_uid="tray-1"
        )

        assert result == 42
        assert upsert.await_args.kwargs["tactical_asset_id"] == "agent-1"
        link.assert_awaited_once_with(9, 42)

    asyncio.run(run())


def test_trmm_sync_endpoint_links_enrolled_device(monkeypatch):
    async def run():
        monkeypatch.setattr(
            tray_routes.tray_repo,
            "get_device_by_uid",
            AsyncMock(return_value={"id": 9, "company_id": 7, "status": "active"}),
        )
        sync = AsyncMock(return_value=42)
        monkeypatch.setattr(tray_routes.asset_importer, "sync_tactical_agent", sync)

        response = await tray_routes.sync_trmm_agent(
            TrayTRMMSyncRequest(agent_id=" agent-1 ", tray_agent_id=" tray-1 "),
            {"id": 3},
        )

        assert response.status == "linked"
        assert response.asset_id == 42
        sync.assert_awaited_once_with(7, agent_id="agent-1", tray_device_uid="tray-1")

    asyncio.run(run())
