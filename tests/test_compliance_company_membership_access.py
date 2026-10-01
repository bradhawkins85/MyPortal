"""Regression tests: compliance and Essential 8 APIs check real membership.

Previously the routes trusted ``user["company_id"]``; they now require an
active membership in the requested company (super admins bypass).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import compliance_checks as compliance_routes
from app.api.routes import essential8 as essential8_routes


@pytest.mark.asyncio
async def test_compliance_checks_rejects_user_without_membership(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(compliance_routes.user_company_repo, "get_user_company", lookup)
    list_assignments = AsyncMock(return_value=[])
    monkeypatch.setattr(compliance_routes.repo, "list_assignments", list_assignments)

    # company_id on the user record matches, but there is no active membership.
    with pytest.raises(HTTPException) as exc:
        await compliance_routes.get_assignment_summary(4, None, {"id": 7, "company_id": 4})
    assert exc.value.status_code == 403
    lookup.assert_awaited_once_with(7, 4)


@pytest.mark.asyncio
async def test_compliance_checks_allows_member_and_super_admin(monkeypatch):
    monkeypatch.setattr(
        compliance_routes.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"user_id": 7, "company_id": 9}),
    )
    summary = AsyncMock(return_value={"total": 0})
    monkeypatch.setattr(compliance_routes.repo, "get_assignment_summary", summary)
    assert await compliance_routes.get_assignment_summary(9, None, {"id": 7, "company_id": 4}) == {"total": 0}
    assert await compliance_routes.get_assignment_summary(3, None, {"id": 1, "is_super_admin": True}) == {"total": 0}


@pytest.mark.asyncio
async def test_essential8_read_routes_reject_user_without_membership(monkeypatch):
    monkeypatch.setattr(essential8_routes.user_company_repo, "get_user_company", AsyncMock(return_value=None))
    repo_calls = AsyncMock(return_value=[])
    monkeypatch.setattr(essential8_routes.essential8_repo, "list_company_compliance", repo_calls)
    monkeypatch.setattr(essential8_routes.essential8_repo, "get_company_compliance_summary", repo_calls)
    monkeypatch.setattr(essential8_routes.essential8_repo, "list_compliance_audit", repo_calls)
    monkeypatch.setattr(essential8_routes.essential8_repo, "get_control_with_requirements", repo_calls)
    user = {"id": 7, "company_id": 4}

    for call in (
        essential8_routes.list_company_compliance(4, None, None, user),
        essential8_routes.get_company_compliance_summary(4, None, user),
        essential8_routes.list_compliance_audit(4, 1, 100, None, user),
        essential8_routes.get_control_with_requirements(1, 4, None, user),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403
    repo_calls.assert_not_called()


@pytest.mark.asyncio
async def test_essential8_read_allows_active_member(monkeypatch):
    monkeypatch.setattr(
        essential8_routes.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"user_id": 7, "company_id": 4}),
    )
    monkeypatch.setattr(
        essential8_routes.essential8_repo,
        "list_company_compliance",
        AsyncMock(return_value=[]),
    )
    assert await essential8_routes.list_company_compliance(4, None, None, {"id": 7, "company_id": 2}) == []
