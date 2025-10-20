from __future__ import annotations

import pytest

from app.repositories import staff


class _DummyStaffDB:
    def __init__(self, rows):
        self.rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    async def fetch_all(self, sql, params):
        self.last_sql = sql.strip()
        self.last_params = params
        return self.rows


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_enabled_staff_users_returns_rows(monkeypatch):
    dummy_rows = [
        {"id": 11, "email": "bravo@example.com"},
        {"id": 5, "email": "alpha@example.com"},
    ]
    dummy_db = _DummyStaffDB(dummy_rows)
    monkeypatch.setattr(staff, "db", dummy_db)

    result = await staff.list_enabled_staff_users(3)

    assert dummy_db.last_params == (3,)
    assert "FROM staff AS s" in (dummy_db.last_sql or "")
    assert result == dummy_rows
    assert result is not dummy_rows


@pytest.mark.anyio
async def test_list_enabled_staff_users_handles_empty(monkeypatch):
    dummy_db = _DummyStaffDB([])
    monkeypatch.setattr(staff, "db", dummy_db)

    result = await staff.list_enabled_staff_users(9)

    assert result == []
    assert dummy_db.last_params == (9,)
