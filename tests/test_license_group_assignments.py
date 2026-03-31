"""Tests for license staff listing including group-based memberships.

Verifies that list_staff_for_license returns:
- Staff directly assigned via staff_licenses
- Staff assigned via group_licenses + office_group_members
- Deduplicated results when a staff member has both a direct and group assignment
"""
from __future__ import annotations

import asyncio

from app.repositories import licenses as license_repo


def test_list_staff_for_license_includes_group_members(monkeypatch):
    """list_staff_for_license must query both direct assignments and group-based
    memberships using a UNION across staff_licenses and group_licenses."""
    captured: list[tuple[str, tuple]] = []

    async def fake_fetch_all(sql: str, params: tuple) -> list[dict]:
        captured.append((sql.strip(), params))
        return []

    monkeypatch.setattr(license_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(license_repo.list_staff_for_license(7))

    assert len(captured) == 1
    sql, params = captured[0]

    assert "group_licenses" in sql.lower(), (
        "Query must join group_licenses to include group-based assignments"
    )
    assert "office_group_members" in sql.lower(), (
        "Query must join office_group_members to expand groups to individual staff"
    )
    assert "union" in sql.lower(), (
        "Query must use UNION to combine direct and group-based assignments"
    )
    assert params == (7, 7), (
        "license_id must be passed twice (once for direct, once for group-based)"
    )


def test_list_groups_for_license_query(monkeypatch):
    """list_groups_for_license must query group_licenses joined with office_groups."""
    captured: list[tuple[str, tuple]] = []

    async def fake_fetch_all(sql: str, params: tuple) -> list[dict]:
        captured.append((sql.strip(), params))
        return []

    monkeypatch.setattr(license_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(license_repo.list_groups_for_license(3))

    assert len(captured) == 1
    sql, params = captured[0]
    assert "group_licenses" in sql.lower()
    assert "office_groups" in sql.lower()
    assert params == (3,)


def test_link_and_unlink_group_functions_exist():
    """link_group_to_license and unlink_group_from_license must be callable."""
    assert callable(license_repo.link_group_to_license)
    assert callable(license_repo.unlink_group_from_license)
