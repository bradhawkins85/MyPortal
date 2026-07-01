from __future__ import annotations

import pytest

from app.repositories import users


def test_build_safe_update_clause_rejects_unknown_columns():
    with pytest.raises(ValueError, match="Unsupported update fields"):
        users._build_safe_update_clause({"email = 'x' --": "bad"})


def test_build_safe_update_clause_allows_known_columns():
    columns, params = users._build_safe_update_clause(
        {"first_name": "Alice", "is_active": 1}
    )
    assert columns == "first_name = %s, is_active = %s"
    assert params == ["Alice", 1]


@pytest.mark.asyncio
async def test_update_user_rejects_unknown_columns_before_query(monkeypatch):
    executed = False

    async def fake_execute(*args, **kwargs):
        nonlocal executed
        executed = True

    monkeypatch.setattr(users.db, "execute", fake_execute)

    with pytest.raises(ValueError, match="Unsupported update fields"):
        await users.update_user(1, **{"email = 'x' --": "bad"})
    assert not executed
