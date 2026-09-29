"""BC5/BC11 plans are scoped to the caller's active company."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.dependencies import bc_rbac
from app.repositories import bc3 as bc_repo


def _request(plan_id):
    return SimpleNamespace(path_params={"plan_id": plan_id} if plan_id is not None else {})


@pytest.fixture
def plan_lookup(monkeypatch):
    async def fake_get_plan(plan_id):
        return {"id": plan_id, "org_id": 5} if plan_id == 1 else ({"id": 2, "org_id": None} if plan_id == 2 else None)

    monkeypatch.setattr(bc_repo, "get_plan_by_id", fake_get_plan)


@pytest.mark.anyio("asyncio")
async def test_plan_in_active_company_is_allowed(plan_lookup):
    await bc_rbac.require_bc_plan_in_active_company(_request("1"), {"id": 3}, 5)


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("plan_id,company_id", [("1", 6), ("1", None), ("2", 5)])
async def test_plan_outside_active_company_is_hidden(plan_lookup, plan_id, company_id):
    with pytest.raises(HTTPException) as exc:
        await bc_rbac.require_bc_plan_in_active_company(_request(plan_id), {"id": 3}, company_id)
    assert exc.value.status_code == 404


@pytest.mark.anyio("asyncio")
async def test_super_admin_reaches_any_plan(plan_lookup):
    await bc_rbac.require_bc_plan_in_active_company(_request("1"), {"id": 1, "is_super_admin": True}, 9)


def test_new_plans_use_active_company_for_non_admins():
    assert bc_rbac.resolve_plan_company_id({"id": 3}, 5, requested=99) == 5
    assert bc_rbac.resolve_plan_company_id({"id": 1, "is_super_admin": True}, 5, requested=99) == 99
    with pytest.raises(HTTPException):
        bc_rbac.resolve_plan_company_id({"id": 3}, None)
