"""Technicians keep access to open assets after switching company.

A technician may have assets from several companies open in different tabs.
Single-asset pages resolve against the asset's own company for users who may
manage assets there, while everyone else stays scoped to the active company.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import main as main_module
from app.features.assets import routes

ACTIVE_COMPANY = 1
ASSET_COMPANY = 2
ASSET_ID = 50

TECH_MEMBERSHIP = {"menu_permissions": {"menu.assets": "write"}}
READ_MEMBERSHIP = {"menu_permissions": {"menu.assets": "read"}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _install(monkeypatch, *, user, memberships):
    async def fake_auth(_request):
        return dict(user), None

    async def fake_membership(_request, _user_id, company_id):
        return memberships.get(company_id)

    async def fake_company(company_id):
        return {"id": company_id}

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_auth)
    monkeypatch.setattr(main_module, "_get_effective_company_membership", fake_membership)
    monkeypatch.setattr(routes.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(
        routes.asset_repo, "get_asset_by_id",
        AsyncMock(return_value={"id": ASSET_ID, "company_id": ASSET_COMPANY}),
    )


@pytest.mark.anyio
async def test_technician_resolves_to_the_asset_company(monkeypatch):
    _install(monkeypatch, user={"id": 7, "company_id": ACTIVE_COMPANY},
             memberships={ACTIVE_COMPANY: TECH_MEMBERSHIP, ASSET_COMPANY: TECH_MEMBERSHIP})

    _user, membership, company, company_id, redirect = await routes._load_asset_context(
        object(), asset_id=ASSET_ID)

    assert redirect is None
    assert company_id == ASSET_COMPANY
    assert company == {"id": ASSET_COMPANY}
    assert membership is TECH_MEMBERSHIP


@pytest.mark.anyio
async def test_super_admin_resolves_to_the_asset_company(monkeypatch):
    _install(monkeypatch, user={"id": 1, "company_id": ACTIVE_COMPANY, "is_super_admin": True},
             memberships={})

    *_rest, company_id, redirect = await routes._load_asset_context(object(), asset_id=ASSET_ID)

    assert redirect is None
    assert company_id == ASSET_COMPANY


@pytest.mark.anyio
async def test_read_only_user_stays_in_the_active_company(monkeypatch):
    _install(monkeypatch, user={"id": 7, "company_id": ACTIVE_COMPANY},
             memberships={ACTIVE_COMPANY: READ_MEMBERSHIP, ASSET_COMPANY: READ_MEMBERSHIP})

    *_rest, company_id, _redirect = await routes._load_asset_context(object(), asset_id=ASSET_ID)

    assert company_id == ACTIVE_COMPANY


@pytest.mark.anyio
async def test_technician_without_access_to_the_asset_company_stays_scoped(monkeypatch):
    _install(monkeypatch, user={"id": 7, "company_id": ACTIVE_COMPANY},
             memberships={ACTIVE_COMPANY: TECH_MEMBERSHIP})

    *_rest, company_id, _redirect = await routes._load_asset_context(object(), asset_id=ASSET_ID)

    assert company_id == ACTIVE_COMPANY


@pytest.mark.anyio
async def test_list_pages_ignore_the_asset_company(monkeypatch):
    _install(monkeypatch, user={"id": 7, "company_id": ACTIVE_COMPANY},
             memberships={ACTIVE_COMPANY: TECH_MEMBERSHIP, ASSET_COMPANY: TECH_MEMBERSHIP})

    *_rest, company_id, _redirect = await routes._load_asset_context(object())

    assert company_id == ACTIVE_COMPANY
    routes.asset_repo.get_asset_by_id.assert_not_awaited()


@pytest.mark.anyio
async def test_technician_can_save_an_asset_from_another_company(monkeypatch):
    _install(monkeypatch, user={"id": 7, "company_id": ACTIVE_COMPANY},
             memberships={ACTIVE_COMPANY: TECH_MEMBERSHIP, ASSET_COMPANY: TECH_MEMBERSHIP})
    set_type = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "set_asset_type", set_type)
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(main_module, "flash_redirect", lambda url, *_args: url)

    class FakeRequest:
        async def form(self):
            return {"asset_type": "laptop"}

    result = await routes.update_asset_type(FakeRequest(), ASSET_ID)

    assert result == f"/assets/{ASSET_ID}"
    set_type.assert_awaited_once_with(ASSET_COMPANY, ASSET_ID, "laptop")


@pytest.mark.anyio
async def test_asset_page_lists_scripts_from_the_asset_company(monkeypatch):
    from app.features.rmm import routes as rmm_routes

    membership = {"menu_permissions": {"menu.assets": "write", "menu.rmm_scripts": "write"}}
    _install(monkeypatch, user={"id": 7, "company_id": ACTIVE_COMPANY},
             memberships={ACTIVE_COMPANY: membership, ASSET_COMPANY: membership})
    monkeypatch.setattr(rmm_routes.assets_repo, "get_asset_by_id",
                        AsyncMock(return_value={"id": ASSET_ID, "company_id": ASSET_COMPANY}))
    list_scripts = AsyncMock(return_value=[])
    monkeypatch.setattr(rmm_routes.rmm_repo, "list_scripts", list_scripts)
    monkeypatch.setattr(rmm_routes.rmm_repo, "get_asset_agent", AsyncMock(return_value=None))

    result = await rmm_routes.list_scripts_api(object(), asset_id=ASSET_ID)

    assert result == {"scripts": []}
    list_scripts.assert_awaited_once_with(company_id=ASSET_COMPANY)
