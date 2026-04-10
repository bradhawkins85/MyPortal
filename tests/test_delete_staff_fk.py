"""Regression tests for staff-delete foreign-key safety.

Ensures that delete_staff and delete_m365_staff_not_in remove staff_licenses
rows BEFORE deleting the staff record so that the FK constraint
  staff_licenses_ibfk_1 (staff_licenses.staff_id -> staff.id)
is never violated.
"""
from __future__ import annotations

import asyncio

from app.repositories import staff as staff_repo


def test_delete_staff_removes_staff_licenses_first(monkeypatch):
    """delete_staff must DELETE from staff_licenses before staff."""
    executed: list[tuple[str, tuple]] = []

    async def fake_execute(sql: str, params: tuple) -> None:
        executed.append((sql.strip(), params))

    monkeypatch.setattr(staff_repo.db, "execute", fake_execute)

    asyncio.run(staff_repo.delete_staff(99))

    assert len(executed) == 2, "Expected exactly two SQL statements"

    first_sql, first_params = executed[0]
    assert "staff_licenses" in first_sql.lower(), (
        "First DELETE must target staff_licenses to avoid FK violation"
    )
    assert first_params == (99,)

    second_sql, second_params = executed[1]
    assert "staff_licenses" not in second_sql.lower() and "staff" in second_sql.lower(), (
        "Second DELETE must target staff"
    )
    assert second_params == (99,)


def test_delete_m365_staff_not_in_removes_staff_licenses_first(monkeypatch):
    """delete_m365_staff_not_in must DELETE from staff_licenses before staff."""
    executed: list[tuple[str, tuple]] = []

    async def fake_fetch_all(sql: str, params: tuple | None = None) -> list[dict]:
        # Return one M365 staff member whose email is NOT in the keep set.
        return [{"id": 7, "email": "gone@example.com"}]

    async def fake_execute(sql: str, params: tuple) -> None:
        executed.append((sql.strip(), params))

    monkeypatch.setattr(staff_repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(staff_repo.db, "execute", fake_execute)

    result = asyncio.run(staff_repo.delete_m365_staff_not_in(1, {"keep@example.com"}))

    assert result == 1, "Expected one record deleted"
    assert len(executed) == 2, "Expected exactly two SQL statements per deleted staff"

    first_sql, first_params = executed[0]
    assert "staff_licenses" in first_sql.lower(), (
        "First DELETE must target staff_licenses to avoid FK violation"
    )
    assert first_params == (7,)

    second_sql, second_params = executed[1]
    assert "staff_licenses" not in second_sql.lower() and "staff" in second_sql.lower(), (
        "Second DELETE must target staff"
    )
    assert second_params == (7,)
