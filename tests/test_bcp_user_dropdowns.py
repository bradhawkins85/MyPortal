"""BCP user pickers only list the company's members and super admins."""

import pytest

from app.api.routes import bcp as bcp_routes
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo


@pytest.mark.anyio("asyncio")
async def test_company_user_list_excludes_other_companies(monkeypatch):
    async def fake_list_users():
        return [
            {"id": 1, "email": "admin@msp.example", "is_super_admin": 1},
            {"id": 2, "email": "member@a.example", "is_super_admin": 0},
            {"id": 3, "email": "outsider@b.example", "is_super_admin": 0},
        ]

    async def fake_assignments(company_id):
        assert company_id == 10
        return [{"user_id": 2, "company_id": 10}]

    monkeypatch.setattr(user_repo, "list_users", fake_list_users)
    monkeypatch.setattr(user_company_repo, "list_assignments", fake_assignments)

    users = await bcp_routes._list_company_users(10)

    assert [user["id"] for user in users] == [1, 2]
