"""Test that the ticket requester API returns correct user IDs."""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_list_company_staff_users_returns_user_ids(monkeypatch):
    """Verify that the staff-users endpoint returns user records with user IDs."""
    from app.api.routes import companies
    from app.repositories import companies as company_repo
    from app.repositories import staff as staff_repo

    # Mock the company lookup
    async def fake_get_company(company_id):
        if company_id == 1:
            return {"id": 1, "name": "Test Company"}
        return None

    # Mock the staff users query - this returns users from the users table
    async def fake_list_enabled_staff_users(company_id):
        if company_id == 1:
            return [
                {
                    "id": 100,  # This is a USER ID from users table
                    "email": "john@example.com",
                    "first_name": "John",
                    "last_name": "Doe",
                    "company_id": 1,
                    "created_at": None,
                    "updated_at": None,
                    "is_super_admin": False,
                },
                {
                    "id": 101,  # This is a USER ID from users table
                    "email": "jane@example.com",
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "company_id": 1,
                    "created_at": None,
                    "updated_at": None,
                    "is_super_admin": False,
                },
            ]
        return []

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(staff_repo, "list_enabled_staff_users", fake_list_enabled_staff_users)

    # Test the endpoint function directly
    result = await companies.list_company_staff_users(
        company_id=1,
        _=None,
        __={"id": 1, "is_super_admin": True},  # Mock current user
    )

    # Verify we get UserResponse objects with the correct user IDs
    assert len(result) == 2
    assert result[0].id == 100
    assert result[0].email == "john@example.com"
    assert result[1].id == 101
    assert result[1].email == "jane@example.com"


@pytest.mark.anyio
async def test_list_company_staff_users_company_not_found(monkeypatch):
    """Verify that the endpoint returns 404 for non-existent company."""
    from app.api.routes import companies
    from app.repositories import companies as company_repo

    async def fake_get_company(company_id):
        return None

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)

    # Test that non-existent company raises HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await companies.list_company_staff_users(
            company_id=999,
            _=None,
            __={"id": 1, "is_super_admin": True},
        )
    
    assert exc_info.value.status_code == 404
    assert "Company not found" in exc_info.value.detail
