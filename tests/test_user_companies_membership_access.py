import json
from unittest.mock import AsyncMock

import pytest

from app.repositories import user_companies


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_list_companies_for_user_includes_active_membership_only_assignments(monkeypatch):
    legacy_rows = [
        {
            "user_id": 7,
            "company_id": 10,
            "company_name": "Legacy Co",
            "syncro_company_id": None,
            "can_access_shop": 1,
            "can_access_cart": 0,
            "staff_permission": 0,
            "role_permissions": json.dumps({"menu": {"menu.shop": "read"}}),
        }
    ]
    membership_rows = [
        {
            "user_id": 7,
            "company_id": 20,
            "company_name": "Membership Co",
            "syncro_company_id": "syncro-20",
            "role_permissions": json.dumps({"menu": {"menu.quotes": "read"}}),
        },
        {
            "user_id": 7,
            "company_id": 10,
            "company_name": "Legacy Co Duplicate",
            "syncro_company_id": None,
            "role_permissions": json.dumps({"menu": {"menu.forms": "read"}}),
        },
    ]

    fetch_all = AsyncMock(side_effect=[legacy_rows, membership_rows])
    monkeypatch.setattr(user_companies.db, "fetch_all", fetch_all)

    result = await user_companies.list_companies_for_user(7)

    assert [company["company_id"] for company in result] == [10, 20]
    membership_company = next(company for company in result if company["company_id"] == 20)
    assert membership_company["company_name"] == "Membership Co"
    assert membership_company["syncro_company_id"] == "syncro-20"
    assert membership_company["can_access_quotes"] is True


@pytest.mark.anyio("asyncio")
async def test_get_user_company_falls_back_to_active_membership(monkeypatch):
    membership_row = {
        "user_id": 7,
        "company_id": 20,
        "company_name": "Membership Co",
        "syncro_company_id": None,
        "role_permissions": json.dumps({"menu": {"menu.forms": "read"}}),
    }

    fetch_one = AsyncMock(side_effect=[None, membership_row])
    monkeypatch.setattr(user_companies.db, "fetch_one", fetch_one)

    result = await user_companies.get_user_company(7, 20)

    assert result is not None
    assert result["user_id"] == 7
    assert result["company_id"] == 20
    assert result["company_name"] == "Membership Co"
    assert result["can_access_forms"] is True
    assert result["is_admin"] is False
