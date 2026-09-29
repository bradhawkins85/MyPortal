"""Regression tests: licence read APIs must enforce company membership."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import licenses as licenses_routes

LICENSE = {
    "id": 5,
    "company_id": 4,
    "name": "M365 E3",
    "platform": "SKU",
    "count": 10,
    "allocated": 2,
    "expiry_date": None,
    "contract_term": None,
    "auto_renew": None,
}


@pytest.fixture
def license_env(monkeypatch):
    monkeypatch.setattr(licenses_routes.license_repo, "get_license_by_id", AsyncMock(return_value=dict(LICENSE)))
    monkeypatch.setattr(licenses_routes.license_repo, "list_staff_for_license", AsyncMock(return_value=[]))
    monkeypatch.setattr(licenses_routes.license_repo, "get_usage_history", AsyncMock(return_value=[{"count": 10}]))
    monkeypatch.setattr(licenses_routes.membership_repo, "user_has_permission", AsyncMock(return_value=False))


def test_other_company_user_cannot_read_license(monkeypatch, license_env):
    monkeypatch.setattr(licenses_routes.user_company_repo, "get_user_company", AsyncMock(return_value=None))
    user = {"id": 9, "company_id": 7}
    for call in (
        licenses_routes.get_license(5, None, user),
        licenses_routes.list_license_staff(5, None, user),
        licenses_routes.get_license_usage_history(5, None, user),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == 404


def test_member_without_license_permission_is_rejected(monkeypatch, license_env):
    monkeypatch.setattr(
        licenses_routes.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"user_id": 9, "company_id": 4, "can_manage_licenses": False}),
    )
    with pytest.raises(HTTPException):
        asyncio.run(licenses_routes.get_license(5, None, {"id": 9}))


def test_member_with_license_permission_can_read(monkeypatch, license_env):
    get_membership = AsyncMock(
        return_value={"user_id": 9, "company_id": 4, "can_manage_licenses": True}
    )
    monkeypatch.setattr(licenses_routes.user_company_repo, "get_user_company", get_membership)
    result = asyncio.run(licenses_routes.get_license(5, None, {"id": 9}))
    assert result.id == 5
    get_membership.assert_awaited_once_with(9, 4)


def test_super_admin_and_technician_can_read(monkeypatch, license_env):
    assert asyncio.run(licenses_routes.list_license_staff(5, None, {"id": 1, "is_super_admin": True})) == []
    monkeypatch.setattr(licenses_routes.membership_repo, "user_has_permission", AsyncMock(return_value=True))
    response = asyncio.run(licenses_routes.get_license_usage_history(5, None, {"id": 2}))
    assert response.status_code == 200
