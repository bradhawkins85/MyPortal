"""Test role-based permission enrichment for company memberships."""

import json
from unittest.mock import AsyncMock

import pytest

from app.repositories import user_companies as user_company_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_enrich_with_role_permissions_maps_licenses_manage(monkeypatch):
    """Test that licenses.manage permission is mapped to can_manage_licenses."""
    
    async def mock_fetch_one(sql: str, params):
        return {
            "user_id": 1,
            "company_id": 2,
            "can_manage_licenses": 0,
            "can_manage_staff": 0,
            "staff_permission": 0,
            "can_manage_assets": 0,
            "can_manage_invoices": 0,
            "can_manage_office_groups": 0,
            "can_manage_issues": 0,
            "can_order_licenses": 0,
            "can_access_shop": 0,
            "can_access_cart": 0,
            "can_access_orders": 0,
            "can_access_forms": 0,
            "can_view_compliance": 0,
            "can_view_bcp": 0,
            "is_admin": 0,
            "role_permissions": json.dumps(["licenses.manage", "portal.access"]),
        }
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_one", mock_fetch_one)
    
    result = await user_company_repo.get_user_company(1, 2)
    
    assert result is not None
    assert result["can_manage_licenses"] is True
    assert result["can_manage_staff"] is False


@pytest.mark.anyio("asyncio")
async def test_enrich_with_role_permissions_maps_multiple_permissions(monkeypatch):
    """Test that multiple role permissions are mapped correctly."""
    
    async def mock_fetch_one(sql: str, params):
        return {
            "user_id": 1,
            "company_id": 2,
            "can_manage_licenses": 0,
            "can_manage_staff": 0,
            "staff_permission": 0,
            "can_manage_assets": 0,
            "can_manage_invoices": 0,
            "can_manage_office_groups": 0,
            "can_manage_issues": 0,
            "can_order_licenses": 0,
            "can_access_shop": 0,
            "can_access_cart": 0,
            "can_access_orders": 0,
            "can_access_forms": 0,
            "can_view_compliance": 0,
            "can_view_bcp": 0,
            "is_admin": 0,
            "role_permissions": json.dumps([
                "licenses.manage",
                "shop.access",
                "cart.access",
                "orders.access",
                "company.admin",
            ]),
        }
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_one", mock_fetch_one)
    
    result = await user_company_repo.get_user_company(1, 2)
    
    assert result is not None
    assert result["can_manage_licenses"] is True
    assert result["can_access_shop"] is True
    assert result["can_access_cart"] is True
    assert result["can_access_orders"] is True
    assert result["is_admin"] is True
    # Permissions not in the role should be False
    assert result["can_manage_staff"] is False
    assert result["can_manage_assets"] is False


@pytest.mark.anyio("asyncio")
async def test_enrich_with_role_permissions_overrides_legacy_values(monkeypatch):
    """Test that role permissions override legacy True values in user_companies."""
    
    async def mock_fetch_one(sql: str, params):
        return {
            "user_id": 1,
            "company_id": 2,
            "can_manage_licenses": 1,  # Already set to True in user_companies (legacy)
            "can_manage_staff": 0,
            "staff_permission": 2,
            "can_manage_assets": 0,
            "can_manage_invoices": 0,
            "can_manage_office_groups": 0,
            "can_manage_issues": 0,
            "can_order_licenses": 0,
            "can_access_shop": 1,  # Already set to True in user_companies (legacy)
            "can_access_cart": 0,
            "can_access_orders": 0,
            "can_access_forms": 0,
            "can_view_compliance": 0,
            "can_view_bcp": 0,
            "is_admin": 0,
            "role_permissions": json.dumps(["shop.access"]),  # Role only has shop.access
        }
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_one", mock_fetch_one)
    
    result = await user_company_repo.get_user_company(1, 2)
    
    assert result is not None
    # Role permissions should override legacy values
    # can_manage_licenses should be False because role doesn't have licenses.manage
    assert result["can_manage_licenses"] is False
    # can_access_shop should be True because role has shop.access
    assert result["can_access_shop"] is True


@pytest.mark.anyio("asyncio")
async def test_enrich_with_role_permissions_handles_null_permissions(monkeypatch):
    """Test that null role permissions are handled gracefully."""
    
    async def mock_fetch_one(sql: str, params):
        return {
            "user_id": 1,
            "company_id": 2,
            "can_manage_licenses": 0,
            "can_manage_staff": 0,
            "staff_permission": 0,
            "can_manage_assets": 0,
            "can_manage_invoices": 0,
            "can_manage_office_groups": 0,
            "can_manage_issues": 0,
            "can_order_licenses": 0,
            "can_access_shop": 0,
            "can_access_cart": 0,
            "can_access_orders": 0,
            "can_access_forms": 0,
            "can_view_compliance": 0,
            "can_view_bcp": 0,
            "is_admin": 0,
            "role_permissions": None,  # No role assigned
        }
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_one", mock_fetch_one)
    
    result = await user_company_repo.get_user_company(1, 2)
    
    assert result is not None
    # All permissions should remain False when no role is assigned
    assert result["can_manage_licenses"] is False
    assert result["can_access_shop"] is False


@pytest.mark.anyio("asyncio")
async def test_list_companies_for_user_enriches_permissions(monkeypatch):
    """Test that list_companies_for_user enriches permissions from roles."""
    
    async def mock_fetch_all(sql: str, params):
        return [
            {
                "user_id": 1,
                "company_id": 2,
                "company_name": "Test Company",
                "syncro_company_id": None,
                "can_manage_licenses": 0,
                "can_manage_staff": 0,
                "staff_permission": 0,
                "can_manage_assets": 0,
                "can_manage_invoices": 0,
                "can_manage_office_groups": 0,
                "can_manage_issues": 0,
                "can_order_licenses": 0,
                "can_access_shop": 0,
                "can_access_cart": 0,
                "can_access_orders": 0,
                "can_access_forms": 0,
                "can_view_compliance": 0,
                "can_view_bcp": 0,
                "is_admin": 0,
                "role_permissions": json.dumps(["licenses.manage", "invoices.manage"]),
            }
        ]
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_all", mock_fetch_all)
    
    results = await user_company_repo.list_companies_for_user(1)
    
    assert len(results) == 1
    assert results[0]["can_manage_licenses"] is True
    assert results[0]["can_manage_invoices"] is True
    assert results[0]["can_access_shop"] is False


@pytest.mark.anyio("asyncio")
async def test_enrich_with_compliance_and_continuity_permissions(monkeypatch):
    """Test that compliance.access and continuity.access permissions are mapped correctly."""
    
    async def mock_fetch_one(sql: str, params):
        return {
            "user_id": 1,
            "company_id": 2,
            "can_manage_licenses": 0,
            "can_manage_staff": 0,
            "staff_permission": 0,
            "can_manage_assets": 0,
            "can_manage_invoices": 0,
            "can_manage_office_groups": 0,
            "can_manage_issues": 0,
            "can_order_licenses": 0,
            "can_access_shop": 0,
            "can_access_cart": 0,
            "can_access_orders": 0,
            "can_access_forms": 0,
            "can_view_compliance": 0,
            "can_view_bcp": 0,
            "is_admin": 0,
            "role_permissions": json.dumps([
                "compliance.access",
                "continuity.access",
                "portal.access",
            ]),
        }
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_one", mock_fetch_one)
    
    result = await user_company_repo.get_user_company(1, 2)
    
    assert result is not None
    assert result["can_view_compliance"] is True
    assert result["can_view_bcp"] is True
    # Other permissions should be False
    assert result["can_access_shop"] is False
    assert result["can_manage_assets"] is False


@pytest.mark.anyio("asyncio")
async def test_enrich_without_compliance_continuity_sets_false(monkeypatch):
    """Test that missing compliance/continuity permissions are set to False."""
    
    async def mock_fetch_one(sql: str, params):
        return {
            "user_id": 1,
            "company_id": 2,
            "can_manage_licenses": 0,
            "can_manage_staff": 0,
            "staff_permission": 0,
            "can_manage_assets": 0,
            "can_manage_invoices": 0,
            "can_manage_office_groups": 0,
            "can_manage_issues": 0,
            "can_order_licenses": 0,
            "can_access_shop": 0,
            "can_access_cart": 0,
            "can_access_orders": 0,
            "can_access_forms": 0,
            "can_view_compliance": 1,  # Set to True in legacy data
            "can_view_bcp": 1,  # Set to True in legacy data
            "is_admin": 0,
            "role_permissions": json.dumps([
                "shop.access",
                "portal.access",
            ]),
        }
    
    monkeypatch.setattr("app.repositories.user_companies.db.fetch_one", mock_fetch_one)
    
    result = await user_company_repo.get_user_company(1, 2)
    
    assert result is not None
    # Legacy True values should be overridden to False because role doesn't have the permissions
    assert result["can_view_compliance"] is False
    assert result["can_view_bcp"] is False
    # Role has shop.access so it should be True
    assert result["can_access_shop"] is True
