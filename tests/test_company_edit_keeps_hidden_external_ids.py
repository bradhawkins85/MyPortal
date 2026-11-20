"""Saving a company keeps the IDs of integrations whose fields are hidden (module disabled)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

import app.main as main_module
from app.features.companies import handlers
from app.repositories import companies as company_repo

EXISTING = {
    "id": 4, "name": "Contoso", "syncro_company_id": "S-1", "tacticalrmm_client_id": "7",
    "xero_id": "X-1", "hudu_id": "H-1", "huntress_organization_id": None,
    "huntress_sat_account_id": "SAT-1", "trello_board_id": "B-1",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _save(monkeypatch, fields: list[tuple[str, str]]) -> dict:
    request = Request({"type": "http", "method": "POST", "path": "/admin/companies/4", "headers": [],
                       "query_string": b""})
    monkeypatch.setattr(request, "form", AsyncMock(return_value=FormData(fields)))
    monkeypatch.setattr(main_module, "_require_super_admin_page",
                        AsyncMock(return_value=({"id": 1, "is_super_admin": True}, None)))
    monkeypatch.setattr(company_repo, "get_company_by_id", AsyncMock(return_value=dict(EXISTING)))
    update = AsyncMock()
    monkeypatch.setattr(company_repo, "update_company", update)
    await handlers.admin_update_company(4, request)
    update.assert_awaited_once()
    return update.await_args.kwargs


@pytest.mark.anyio
async def test_hidden_integration_ids_are_kept(monkeypatch):
    saved = await _save(monkeypatch, [("name", "Contoso")])

    assert saved["syncro_company_id"] == "S-1"
    assert saved["tacticalrmm_client_id"] == "7"
    assert saved["xero_id"] == "X-1"
    assert saved["hudu_id"] == "H-1"
    assert saved["huntress_sat_account_id"] == "SAT-1"
    assert saved["trello_board_id"] == "B-1"


@pytest.mark.anyio
async def test_shown_integration_ids_can_still_be_cleared(monkeypatch):
    saved = await _save(monkeypatch, [("name", "Contoso"), ("syncroCompanyId", ""),
                                      ("tacticalClientId", " 9 ")])

    assert saved["syncro_company_id"] is None
    assert saved["tacticalrmm_client_id"] == "9"


@pytest.mark.anyio
@pytest.mark.parametrize("trmm_on, published", [(False, False), (True, True)])
async def test_new_tray_token_is_only_sent_to_tactical_rmm_when_enabled(monkeypatch, trmm_on, published):
    from app.repositories import tray as tray_repo
    from app.services import asset_presence
    from app.services import tray as tray_service

    async def module_enabled(slug):
        return trmm_on

    request = Request({"type": "http", "method": "POST", "path": "/admin/companies/4/tray/tokens",
                       "headers": [], "query_string": b""})
    monkeypatch.setattr(request, "form", AsyncMock(return_value=FormData([])))
    monkeypatch.setattr(main_module, "_require_super_admin_page",
                        AsyncMock(return_value=({"id": 1, "is_super_admin": True}, None)))
    monkeypatch.setattr(company_repo, "get_company_by_id", AsyncMock(return_value=dict(EXISTING)))
    monkeypatch.setattr(tray_repo, "create_install_token", AsyncMock())
    monkeypatch.setattr(asset_presence, "module_enabled", module_enabled)
    publish = AsyncMock()
    monkeypatch.setattr(tray_service, "update_trmm_client_token_field", publish)

    await handlers.admin_company_tray_create_token(4, request)

    assert publish.await_count == (1 if published else 0)
