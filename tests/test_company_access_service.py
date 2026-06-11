"""Tests for company_access service."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import company_access as company_access_service
from app.services.company_access import _build_super_admin_membership
from app.repositories import companies as company_repo
from app.repositories import user_companies as user_company_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _build_super_admin_membership
# ---------------------------------------------------------------------------


def test_build_super_admin_membership_basic():
    company = {"id": 42, "name": "Acme Corp", "syncro_company_id": "SYN-1"}
    result = _build_super_admin_membership(company)
    assert result["company_id"] == 42
    assert result["company_name"] == "Acme Corp"
    assert result["syncro_company_id"] == "SYN-1"
    assert result["is_admin"] is True
    assert result["can_manage_staff"] is True
    assert result["staff_permission"] == 3


def test_build_super_admin_membership_grants_all_flags():
    company = {"id": 1, "name": "Test"}
    result = _build_super_admin_membership(company)
    expected_flags = [
        "can_manage_licenses",
        "can_manage_office_groups",
        "can_manage_assets",
        "can_manage_invoices",
        "can_manage_issues",
        "can_order_licenses",
        "can_access_shop",
        "can_access_cart",
        "can_access_orders",
        "can_access_forms",
    ]
    for flag in expected_flags:
        assert result.get(flag) is True, f"expected {flag} to be True"


def test_build_super_admin_membership_invalid_id_returns_empty():
    company = {"id": "not-an-int", "name": "Bad Corp"}
    result = _build_super_admin_membership(company)
    assert result == {}


def test_build_super_admin_membership_none_id_returns_empty():
    company = {"id": None, "name": "No ID"}
    result = _build_super_admin_membership(company)
    assert result == {}


def test_build_super_admin_membership_string_numeric_id():
    company = {"id": "5", "name": "String ID Corp"}
    result = _build_super_admin_membership(company)
    assert result["company_id"] == 5


# ---------------------------------------------------------------------------
# list_accessible_companies
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_super_admin_delegates_to_user_company_repo(monkeypatch):
    user = {"id": 7, "is_super_admin": False}
    expected = [{"company_id": 1, "company_name": "Acme"}]

    monkeypatch.setattr(
        user_company_repo,
        "list_companies_for_user",
        AsyncMock(return_value=expected),
    )

    result = await company_access_service.list_accessible_companies(user)
    assert result == expected


@pytest.mark.anyio
async def test_non_super_admin_invalid_user_id_returns_empty(monkeypatch):
    user = {"id": "bad", "is_super_admin": False}

    result = await company_access_service.list_accessible_companies(user)
    assert result == []


@pytest.mark.anyio
async def test_non_super_admin_zero_user_id_returns_empty(monkeypatch):
    user = {"id": 0, "is_super_admin": False}

    result = await company_access_service.list_accessible_companies(user)
    assert result == []


@pytest.mark.anyio
async def test_super_admin_db_not_connected_returns_empty(monkeypatch):
    user = {"id": 1, "is_super_admin": True}

    mock_db = MagicMock()
    mock_db.is_connected.return_value = False
    monkeypatch.setattr(company_access_service, "db", mock_db)

    result = await company_access_service.list_accessible_companies(user)
    assert result == []


@pytest.mark.anyio
async def test_super_admin_returns_all_companies(monkeypatch):
    user = {"id": 1, "is_super_admin": True}

    mock_db = MagicMock()
    mock_db.is_connected.return_value = True
    monkeypatch.setattr(company_access_service, "db", mock_db)

    companies = [
        {"id": 1, "name": "Acme"},
        {"id": 2, "name": "Beta"},
    ]
    monkeypatch.setattr(
        company_repo, "list_companies", AsyncMock(return_value=companies)
    )

    result = await company_access_service.list_accessible_companies(user)
    assert len(result) == 2
    ids = {r["company_id"] for r in result}
    assert ids == {1, 2}


@pytest.mark.anyio
async def test_super_admin_skips_companies_with_invalid_id(monkeypatch):
    user = {"id": 1, "is_super_admin": True}

    mock_db = MagicMock()
    mock_db.is_connected.return_value = True
    monkeypatch.setattr(company_access_service, "db", mock_db)

    companies = [
        {"id": 1, "name": "Valid"},
        {"id": None, "name": "No ID"},
        {"id": "bad", "name": "Bad ID"},
    ]
    monkeypatch.setattr(
        company_repo, "list_companies", AsyncMock(return_value=companies)
    )

    result = await company_access_service.list_accessible_companies(user)
    assert len(result) == 1
    assert result[0]["company_id"] == 1


@pytest.mark.anyio
async def test_super_admin_db_pool_not_initialised_on_second_call_returns_empty(monkeypatch):
    """The second list_companies call (in try-except) handles pool errors gracefully."""
    user = {"id": 1, "is_super_admin": True}

    mock_db = MagicMock()
    mock_db.is_connected.return_value = True
    monkeypatch.setattr(company_access_service, "db", mock_db)

    call_count = {"n": 0}

    async def partial_failure():
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Simulate pool not initialised on the second (protected) call
            raise RuntimeError("Database pool not initialised")
        return [{"id": 1, "name": "Acme"}]

    monkeypatch.setattr(company_repo, "list_companies", partial_failure)

    result = await company_access_service.list_accessible_companies(user)
    assert result == []


# ---------------------------------------------------------------------------
# first_accessible_company_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_first_accessible_company_id_from_user_dict(monkeypatch):
    user = {"id": 1, "is_super_admin": False, "company_id": 99}

    result = await company_access_service.first_accessible_company_id(user)
    assert result == 99


@pytest.mark.anyio
async def test_first_accessible_company_id_fallback_to_list(monkeypatch):
    user = {"id": 5, "is_super_admin": False}
    companies = [{"company_id": 7, "company_name": "Alpha"}]

    monkeypatch.setattr(
        user_company_repo,
        "list_companies_for_user",
        AsyncMock(return_value=companies),
    )

    result = await company_access_service.first_accessible_company_id(user)
    assert result == 7


@pytest.mark.anyio
async def test_first_accessible_company_id_no_companies_returns_none(monkeypatch):
    user = {"id": 5, "is_super_admin": False}

    monkeypatch.setattr(
        user_company_repo,
        "list_companies_for_user",
        AsyncMock(return_value=[]),
    )

    result = await company_access_service.first_accessible_company_id(user)
    assert result is None


@pytest.mark.anyio
async def test_technician_access_returns_all_companies_with_role_permissions(monkeypatch):
    user = {"id": 7, "is_super_admin": False}

    mock_db = MagicMock()
    mock_db.is_connected.return_value = True
    monkeypatch.setattr(company_access_service, "db", mock_db)
    monkeypatch.setattr(
        company_access_service.membership_repo,
        "user_has_permission",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        company_access_service.membership_repo,
        "get_first_membership_with_permission",
        AsyncMock(
            return_value={
                "role_id": 4,
                "role_name": "Technician",
                "permissions": {
                    "menu.admin.technician": "write",
                    "menu.assets": "write",
                    "menu.quotes": "read",
                },
            }
        ),
    )
    monkeypatch.setattr(
        company_repo,
        "list_companies",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "Acme", "syncro_company_id": "A-1"},
                {"id": 2, "name": "Beta", "syncro_company_id": "B-1"},
            ]
        ),
    )

    result = await company_access_service.list_accessible_companies(user)

    assert [entry["company_id"] for entry in result] == [1, 2]
    assert all(entry["is_global_company_access"] is True for entry in result)
    assert all(entry["membership_role_name"] == "Technician" for entry in result)
    assert all(entry["can_manage_assets"] is True for entry in result)
    assert all(entry["can_access_quotes"] is True for entry in result)
    assert all(entry["is_admin"] is False for entry in result)
