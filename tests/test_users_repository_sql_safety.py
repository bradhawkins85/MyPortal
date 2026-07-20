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


@pytest.mark.anyio
async def test_update_user_rejects_unknown_columns_before_query(monkeypatch):
    executed = False

    async def fake_execute(*args, **kwargs):
        nonlocal executed
        executed = True

    monkeypatch.setattr(users.db, "execute", fake_execute)

    with pytest.raises(ValueError, match="Unsupported update fields"):
        await users.update_user(1, **{"email = 'x' --": "bad"})
    assert not executed


@pytest.mark.anyio
async def test_get_user_by_phone_normalises_common_phone_formatting(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_one(query, params):
        captured["query"] = query
        captured["params"] = params
        return {"id": 42}

    monkeypatch.setattr(users.db, "fetch_one", fake_fetch_one)

    result = await users.get_user_by_phone(" +1 (555) 010-1234 ")

    assert result == {"id": 42}
    assert captured["params"] == ("15550101234",)
    assert "COALESCE(mobile_phone, '')" in captured["query"]


@pytest.mark.anyio
async def test_get_user_by_phone_ignores_empty_normalised_values(monkeypatch):
    queried = False

    async def fake_fetch_one(*args, **kwargs):
        nonlocal queried
        queried = True

    monkeypatch.setattr(users.db, "fetch_one", fake_fetch_one)

    assert await users.get_user_by_phone("+ - ()") is None
    assert not queried


@pytest.mark.anyio
async def test_get_user_by_email_is_case_insensitive_and_trims(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_one(query, params):
        captured["query"] = query
        captured["params"] = params
        return {"id": 7, "email": "Sender@Example.com"}

    monkeypatch.setattr(users.db, "fetch_one", fake_fetch_one)

    result = await users.get_user_by_email(" sender@example.COM ")

    assert result == {"id": 7, "email": "Sender@Example.com"}
    assert captured["params"] == ("sender@example.COM",)
    assert "LOWER(email) = LOWER(%s)" in captured["query"]


@pytest.mark.anyio
async def test_get_user_by_email_ignores_empty_values(monkeypatch):
    queried = False

    async def fake_fetch_one(*args, **kwargs):
        nonlocal queried
        queried = True

    monkeypatch.setattr(users.db, "fetch_one", fake_fetch_one)

    assert await users.get_user_by_email("   ") is None
    assert not queried
