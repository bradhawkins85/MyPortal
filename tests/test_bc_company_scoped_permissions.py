"""Regression tests: BCP / BC permissions are evaluated on the active company.

A user who holds an edit permission in company A but only read access in
company B must not be able to edit company B's continuity data by switching
their active company to B.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from app.api.dependencies import bc_rbac
from app.api.routes import bcp
from app.services import role_switching

COMPANY_A = 10
COMPANY_B = 20
USER_ID = 42


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _memberships(monkeypatch, per_company: dict[int, list[str]]):
    async def get_membership(company_id, user_id):
        perms = per_company.get(company_id)
        if perms is None:
            return None
        return {"company_id": company_id, "user_id": user_id, "status": "active",
                "legacy_permissions": sorted(perms)}

    monkeypatch.setattr(bc_rbac.membership_repo, "get_membership_by_company_user", get_membership)
    monkeypatch.setattr(
        bc_rbac.user_permissions_repo, "list_user_permissions", AsyncMock(return_value=[])
    )
    # Guard against the legacy any-membership check being used again.
    monkeypatch.setattr(
        bc_rbac.membership_repo, "user_has_permission", AsyncMock(return_value=True)
    )
    from app.repositories import users as user_repo

    monkeypatch.setattr(
        user_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": USER_ID, "is_super_admin": False, "name": "U"}),
    )
    role_switching.clear_effective_role()


@pytest.mark.anyio("asyncio")
async def test_company_permission_only_uses_active_membership(monkeypatch):
    _memberships(monkeypatch, {COMPANY_A: ["bcp:view", "bcp:edit"], COMPANY_B: ["bcp:view"]})

    assert await bc_rbac.user_has_company_permission(USER_ID, COMPANY_A, "bcp:edit") is True
    assert await bc_rbac.user_has_company_permission(USER_ID, COMPANY_B, "bcp:edit") is False
    assert await bc_rbac.user_has_company_permission(USER_ID, COMPANY_B, "bcp:view") is True
    assert await bc_rbac.user_has_company_permission(USER_ID, 999, "bcp:view") is False
    assert await bc_rbac.user_has_company_permission(USER_ID, None, "bcp:view") is False


@pytest.mark.anyio("asyncio")
async def test_inactive_membership_grants_nothing(monkeypatch):
    _memberships(monkeypatch, {COMPANY_A: ["bcp:edit"]})

    async def suspended(company_id, user_id):
        return {"status": "suspended", "legacy_permissions": ["bcp:edit"]}

    monkeypatch.setattr(bc_rbac.membership_repo, "get_membership_by_company_user", suspended)
    assert await bc_rbac.user_has_company_permission(USER_ID, COMPANY_A, "bcp:edit") is False


@pytest.mark.anyio("asyncio")
async def test_bcp_edit_guard_denies_editor_of_other_company(monkeypatch):
    _memberships(monkeypatch, {COMPANY_A: ["bcp:view", "bcp:edit"], COMPANY_B: ["bcp:view"]})
    session = MagicMock(user_id=USER_ID, active_company_id=COMPANY_B)
    request = MagicMock()
    request.state.active_company_id = COMPANY_B

    with pytest.raises(HTTPException) as exc_info:
        await bcp._require_bcp_edit(request, session)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    request.state.active_company_id = COMPANY_A
    user, company_id = await bcp._require_bcp_edit(request, session)
    assert company_id == COMPANY_A


@pytest.mark.anyio("asyncio")
async def test_bc_editor_dependency_scoped_to_active_company(monkeypatch):
    _memberships(monkeypatch, {COMPANY_A: ["bc.viewer", "bc.editor"], COMPANY_B: ["bc.viewer"]})
    user = {"id": USER_ID, "is_super_admin": False}

    with pytest.raises(HTTPException) as exc_info:
        await bc_rbac.require_bc_editor(current_user=dict(user), company_id=COMPANY_B)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    result = await bc_rbac.require_bc_editor(current_user=dict(user), company_id=COMPANY_A)
    assert result["bc_active_company_id"] == COMPANY_A
    # Route handlers re-derive the role from the same active company.
    assert await bc_rbac._get_user_bc_role(result) == bc_rbac.BCUserRole.EDITOR


@pytest.mark.anyio("asyncio")
async def test_super_admin_keeps_full_access(monkeypatch):
    _memberships(monkeypatch, {})
    from app.repositories import users as user_repo

    monkeypatch.setattr(
        user_repo, "get_user_by_id", AsyncMock(return_value={"id": 1, "is_super_admin": True})
    )
    assert await bc_rbac.user_has_company_permission(1, COMPANY_B, "bcp:edit") is True
    admin = {"id": 1, "is_super_admin": True}
    assert await bc_rbac.require_bc_admin(current_user=admin, company_id=None) is admin
