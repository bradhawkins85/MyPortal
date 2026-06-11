from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import user_companies as user_company_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_get_user_company_synthesizes_technician_membership(monkeypatch):
    fetch_one = AsyncMock(side_effect=[None, None])
    monkeypatch.setattr(user_company_repo.db, "fetch_one", fetch_one)
    monkeypatch.setattr(
        user_company_repo.membership_repo,
        "get_first_membership_with_permission",
        AsyncMock(
            return_value={
                "role_id": 4,
                "role_name": "Technician",
                "permissions": {
                    "menu.admin.technician": "write",
                    "menu.assets": "write",
                    "menu.staff": "read",
                },
            }
        ),
    )
    monkeypatch.setattr(
        user_company_repo.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 42, "name": "Remote Co", "syncro_company_id": "RC"}),
    )

    result = await user_company_repo.get_user_company(7, 42)

    assert result is not None
    assert result["company_id"] == 42
    assert result["company_name"] == "Remote Co"
    assert result["is_global_company_access"] is True
    assert result["membership_role_name"] == "Technician"
    assert result["can_manage_assets"] is True
    assert result["can_manage_staff"] is False
    assert result["is_admin"] is False


@pytest.mark.anyio
async def test_get_user_company_does_not_synthesize_without_technician_permission(monkeypatch):
    fetch_one = AsyncMock(side_effect=[None, None])
    monkeypatch.setattr(user_company_repo.db, "fetch_one", fetch_one)
    monkeypatch.setattr(
        user_company_repo.membership_repo,
        "get_first_membership_with_permission",
        AsyncMock(return_value=None),
    )
    company_lookup = AsyncMock()
    monkeypatch.setattr(user_company_repo.company_repo, "get_company_by_id", company_lookup)

    result = await user_company_repo.get_user_company(7, 42)

    assert result is None
    company_lookup.assert_not_awaited()
