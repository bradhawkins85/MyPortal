"""Tests that delete_license removes staff_licenses rows before the license row.

Regression test for the FK constraint error:
  (1451, 'Cannot delete or update a parent row: a foreign key constraint fails
   (myportal.staff_licenses, CONSTRAINT staff_licenses_ibfk_2
   FOREIGN KEY (license_id) REFERENCES licenses (id))')
"""
from __future__ import annotations

import asyncio

from app.repositories import licenses as license_repo


def test_delete_license_removes_staff_licenses_first(monkeypatch):
    """delete_license must DELETE from staff_licenses before licenses so that
    the foreign-key constraint staff_licenses_ibfk_2 is never violated."""
    executed: list[tuple[str, tuple]] = []

    async def fake_execute(sql: str, params: tuple) -> None:
        executed.append((sql.strip(), params))

    monkeypatch.setattr(license_repo.db, "execute", fake_execute)

    asyncio.run(license_repo.delete_license(42))

    assert len(executed) == 2, "Expected exactly two SQL statements"

    first_sql, first_params = executed[0]
    assert "staff_licenses" in first_sql.lower(), (
        "First DELETE must target staff_licenses to satisfy FK constraint"
    )
    assert first_params == (42,)

    second_sql, second_params = executed[1]
    assert "licenses" in second_sql.lower() and "staff_licenses" not in second_sql.lower(), (
        "Second DELETE must target licenses"
    )
    assert second_params == (42,)
