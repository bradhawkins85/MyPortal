"""Tests for company ID lookup API endpoints."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_lookup_tactical_id_found(monkeypatch):
    """Test Tactical RMM ID lookup endpoint when ID is found."""
    from app.api.routes import companies
    from app.services import company_id_lookup
    
    company_data = {
        "id": 1,
        "name": "Test Company",
        "tacticalrmm_client_id": None,
    }
    
    async def fake_get_company(company_id: int):
        return company_data if company_id == 1 else None
    
    async def fake_update_company(company_id: int, **updates):
        company_data.update(updates)
        return company_data
    
    async def fake_lookup_tactical_id(company_name: str):
        if company_name == "Test Company":
            return "tactical-123"
        return None
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(companies.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(
        company_id_lookup,
        "_lookup_tactical_client_id",
        fake_lookup_tactical_id
    )
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            result = await companies.lookup_tactical_rmm_client_id(1)
    
    assert result["status"] == "found"
    assert result["id"] == "tactical-123"
    assert company_data["tacticalrmm_client_id"] == "tactical-123"


@pytest.mark.anyio
async def test_lookup_tactical_id_not_found(monkeypatch):
    """Test Tactical RMM ID lookup endpoint when ID is not found."""
    from app.api.routes import companies
    from app.services import company_id_lookup
    
    company_data = {
        "id": 1,
        "name": "Unknown Company",
        "tacticalrmm_client_id": None,
    }
    
    async def fake_get_company(company_id: int):
        return company_data if company_id == 1 else None
    
    async def fake_lookup_tactical_id(company_name: str):
        return None
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(
        company_id_lookup,
        "_lookup_tactical_client_id",
        fake_lookup_tactical_id
    )
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            result = await companies.lookup_tactical_rmm_client_id(1)
    
    assert result["status"] == "not_found"
    assert result["id"] is None


@pytest.mark.anyio
async def test_lookup_tactical_id_company_not_found(monkeypatch):
    """Test Tactical RMM ID lookup endpoint when company doesn't exist."""
    from app.api.routes import companies
    from fastapi import HTTPException
    
    async def fake_get_company(company_id: int):
        return None
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            with pytest.raises(HTTPException) as exc_info:
                await companies.lookup_tactical_rmm_client_id(999)
    
    assert exc_info.value.status_code == 404
    assert "Company not found" in exc_info.value.detail


@pytest.mark.anyio
async def test_lookup_tactical_id_configuration_error(monkeypatch):
    """Test Tactical RMM ID lookup endpoint when service is not configured."""
    from app.api.routes import companies
    from app.services import company_id_lookup
    from fastapi import HTTPException
    
    company_data = {
        "id": 1,
        "name": "Test Company",
        "tacticalrmm_client_id": None,
    }
    
    async def fake_get_company(company_id: int):
        return company_data if company_id == 1 else None
    
    async def fake_lookup_tactical_id(company_name: str):
        raise company_id_lookup.tacticalrmm.TacticalRMMConfigurationError("Not configured")
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(
        company_id_lookup,
        "_lookup_tactical_client_id",
        fake_lookup_tactical_id
    )
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            with pytest.raises(HTTPException) as exc_info:
                await companies.lookup_tactical_rmm_client_id(1)
    
    assert exc_info.value.status_code == 503
    assert "Tactical RMM not configured" in exc_info.value.detail


@pytest.mark.anyio
async def test_lookup_xero_id_found(monkeypatch):
    """Test Xero ID lookup endpoint when ID is found."""
    from app.api.routes import companies
    from app.services import company_id_lookup
    
    company_data = {
        "id": 1,
        "name": "Test Company",
        "xero_id": None,
    }
    
    async def fake_get_company(company_id: int):
        return company_data if company_id == 1 else None
    
    async def fake_update_company(company_id: int, **updates):
        company_data.update(updates)
        return company_data
    
    async def fake_lookup_xero_id(company_name: str):
        if company_name == "Test Company":
            return "xero-456"
        return None
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(companies.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(
        company_id_lookup,
        "_lookup_xero_contact_id",
        fake_lookup_xero_id
    )
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            result = await companies.lookup_xero_contact_id(1)
    
    assert result["status"] == "found"
    assert result["id"] == "xero-456"
    assert company_data["xero_id"] == "xero-456"


@pytest.mark.anyio
async def test_lookup_xero_id_not_found(monkeypatch):
    """Test Xero ID lookup endpoint when ID is not found."""
    from app.api.routes import companies
    from app.services import company_id_lookup
    
    company_data = {
        "id": 1,
        "name": "Unknown Company",
        "xero_id": None,
    }
    
    async def fake_get_company(company_id: int):
        return company_data if company_id == 1 else None
    
    async def fake_lookup_xero_id(company_name: str):
        return None
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(
        company_id_lookup,
        "_lookup_xero_contact_id",
        fake_lookup_xero_id
    )
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            result = await companies.lookup_xero_contact_id(1)
    
    assert result["status"] == "not_found"
    assert result["id"] is None


@pytest.mark.anyio
async def test_lookup_xero_id_company_not_found(monkeypatch):
    """Test Xero ID lookup endpoint when company doesn't exist."""
    from app.api.routes import companies
    from fastapi import HTTPException
    
    async def fake_get_company(company_id: int):
        return None
    
    monkeypatch.setattr(companies.company_repo, "get_company_by_id", fake_get_company)
    
    # Mock the dependencies
    with patch("app.api.routes.companies.require_database", return_value=None):
        with patch("app.api.routes.companies.require_super_admin", return_value={"id": 1}):
            with pytest.raises(HTTPException) as exc_info:
                await companies.lookup_xero_contact_id(999)
    
    assert exc_info.value.status_code == 404
    assert "Company not found" in exc_info.value.detail
