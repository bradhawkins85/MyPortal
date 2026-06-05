from __future__ import annotations

import pytest

from app.repositories import chat_auto_assign as repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_all_technicians_uses_permission_lookup_not_legacy_column(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        return [
            {"id": 1, "email": "admin@example.com", "first_name": "Admin", "last_name": "User", "matrix_user_id": "", "is_super_admin": 1},
            {"id": 2, "email": "role@example.com", "first_name": "Role", "last_name": "Tech", "matrix_user_id": "", "is_super_admin": 0},
            {"id": 3, "email": "direct@example.com", "first_name": "Direct", "last_name": "Tech", "matrix_user_id": "", "is_super_admin": 0},
            {"id": 4, "email": "none@example.com", "first_name": "No", "last_name": "Access", "matrix_user_id": "", "is_super_admin": 0},
        ]

    async def fake_list_users_with_permission(_permission):
        return [{"id": 2}]

    async def fake_user_has_permission(user_id, _permission):
        return user_id == 3

    monkeypatch.setattr(repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(repo.membership_repo, "list_users_with_permission", fake_list_users_with_permission)
    monkeypatch.setattr(repo.membership_repo, "user_has_permission", fake_user_has_permission)

    technicians = await repo.list_all_technicians()

    assert "is_helpdesk_technician" not in captured["sql"]
    assert [row["id"] for row in technicians] == [1, 3, 2]
    assert 4 not in [row["id"] for row in technicians]


@pytest.mark.anyio
async def test_list_technicians_with_matrix_id_applies_matrix_filter(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        return [
            {"id": 10, "email": "matrix-super@example.com", "first_name": "Alpha", "last_name": "Admin", "matrix_user_id": "@alpha:matrix", "is_super_admin": 1},
            {"id": 11, "email": "matrix-helpdesk@example.com", "first_name": "Bravo", "last_name": "Tech", "matrix_user_id": "@bravo:matrix", "is_super_admin": 0},
        ]

    async def fake_list_users_with_permission(_permission):
        return [{"id": 11}]

    async def fake_user_has_permission(_user_id, _permission):
        return False

    monkeypatch.setattr(repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(repo.membership_repo, "list_users_with_permission", fake_list_users_with_permission)
    monkeypatch.setattr(repo.membership_repo, "user_has_permission", fake_user_has_permission)

    technicians = await repo.list_technicians_with_matrix_id()

    assert "matrix_user_id IS NOT NULL" in captured["sql"]
    assert [row["id"] for row in technicians] == [10, 11]
