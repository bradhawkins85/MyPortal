"""Tests for compliance access control permissions."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main
from app.security.session import SessionData


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/compliance") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_load_compliance_context_denies_without_permission(monkeypatch):
    """Test that users without can_view_compliance permission are denied access."""
    request = _make_request()
    user = {"id": 9, "company_id": 5, "is_super_admin": False}
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(
        main.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"can_view_compliance": 0}),
    )

    result = await main._load_compliance_context(request)

    # Should return a redirect (result[-1] is not None)
    assert result[-1] is not None


@pytest.mark.anyio("asyncio")
async def test_load_compliance_context_allows_with_permission(monkeypatch):
    """Test that users with can_view_compliance permission are granted access."""
    request = _make_request()
    user = {"id": 11, "company_id": 7, "is_super_admin": False}
    membership = {"can_view_compliance": 1}
    company = {"id": 7, "name": "Example Company"}

    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company))

    loaded_user, loaded_membership, loaded_company, company_id, redirect = await main._load_compliance_context(request)

    # Should not redirect
    assert redirect is None
    assert loaded_user == user
    assert loaded_membership == membership
    assert loaded_company == company
    assert company_id == 7


@pytest.mark.anyio("asyncio")
async def test_load_compliance_context_allows_super_admin(monkeypatch):
    """Test that super admins have access to compliance without explicit permission."""
    request = _make_request()
    user = {"id": 1, "company_id": 3, "is_super_admin": True}
    membership = {"can_view_compliance": 0}  # No permission, but is super admin
    company = {"id": 3, "name": "Admin Company"}

    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company))

    loaded_user, loaded_membership, loaded_company, company_id, redirect = await main._load_compliance_context(request)

    # Should not redirect
    assert redirect is None
    assert loaded_user == user
    assert company_id == 3


@pytest.mark.anyio("asyncio")
async def test_compliance_page_requires_permission(monkeypatch):
    """Test that the compliance page enforces permission checks."""
    request = _make_request("/compliance")
    user = {"id": 10, "company_id": 6, "is_super_admin": False}
    
    # Mock _load_compliance_context to return a redirect
    async def mock_load_compliance_context(req):
        from fastapi import status
        from fastapi.responses import RedirectResponse
        return user, None, None, 6, RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    monkeypatch.setattr(main, "_load_compliance_context", mock_load_compliance_context)
    
    result = await main.compliance_page(request)
    
    # Should return a redirect response
    assert hasattr(result, "status_code")
    assert result.status_code == 303


@pytest.mark.anyio("asyncio")
async def test_compliance_control_page_requires_permission(monkeypatch):
    """Test that the compliance control requirements page enforces permission checks."""
    request = _make_request("/compliance/control/1")
    user = {"id": 10, "company_id": 6, "is_super_admin": False}
    
    # Mock _load_compliance_context to return a redirect
    async def mock_load_compliance_context(req):
        from fastapi import status
        from fastapi.responses import RedirectResponse
        return user, None, None, 6, RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    monkeypatch.setattr(main, "_load_compliance_context", mock_load_compliance_context)
    
    result = await main.compliance_control_requirements_page(request, control_id=1)
    
    # Should return a redirect response
    assert hasattr(result, "status_code")
    assert result.status_code == 303
