"""Repository for the Reporting feature (super-admin authored SQL queries)."""
from __future__ import annotations

from typing import Any

from app.core.database import db


# ---------------------------------------------------------------------------
# Reporting queries
# ---------------------------------------------------------------------------

def _normalise_query(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    if "is_system" in record and record["is_system"] is not None:
        try:
            record["is_system"] = bool(int(record["is_system"]))
        except (TypeError, ValueError):
            record["is_system"] = bool(record["is_system"])
    for key in ("id", "created_by"):
        if key in record and record[key] is not None:
            try:
                record[key] = int(record[key])
            except (TypeError, ValueError):
                pass
    return record


async def list_queries() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, slug, name, description, sql_query, is_system, created_by, "
        "created_at, updated_at FROM reporting_queries ORDER BY name ASC"
    )
    return [_normalise_query(row) for row in (rows or []) if row]


async def get_query(query_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, slug, name, description, sql_query, is_system, created_by, "
        "created_at, updated_at FROM reporting_queries WHERE id = %s",
        (int(query_id),),
    )
    return _normalise_query(row)


async def get_query_by_slug(slug: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, slug, name, description, sql_query, is_system, created_by, "
        "created_at, updated_at FROM reporting_queries WHERE slug = %s",
        (slug,),
    )
    return _normalise_query(row)


async def create_query(
    *,
    slug: str,
    name: str,
    description: str | None,
    sql_query: str,
    created_by: int | None,
) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO reporting_queries (slug, name, description, sql_query, is_system, created_by) "
        "VALUES (%s, %s, %s, %s, 0, %s)",
        (slug, name, description, sql_query, created_by),
    )


async def update_query(
    query_id: int,
    *,
    slug: str,
    name: str,
    description: str | None,
    sql_query: str,
) -> None:
    await db.execute(
        "UPDATE reporting_queries SET slug = %s, name = %s, description = %s, "
        "sql_query = %s WHERE id = %s",
        (slug, name, description, sql_query, int(query_id)),
    )


async def delete_query(query_id: int) -> None:
    await db.execute(
        "DELETE FROM reporting_queries WHERE id = %s",
        (int(query_id),),
    )


# ---------------------------------------------------------------------------
# Per-user permissions
# ---------------------------------------------------------------------------


async def list_permission_user_ids(query_id: int) -> list[int]:
    rows = await db.fetch_all(
        "SELECT user_id FROM reporting_query_permissions WHERE query_id = %s",
        (int(query_id),),
    )
    user_ids: list[int] = []
    for row in rows or []:
        user_id = row.get("user_id") if isinstance(row, dict) else row["user_id"]
        if user_id is None:
            continue
        try:
            user_ids.append(int(user_id))
        except (TypeError, ValueError):
            continue
    return user_ids


async def replace_permissions(query_id: int, user_ids: list[int]) -> None:
    await db.execute(
        "DELETE FROM reporting_query_permissions WHERE query_id = %s",
        (int(query_id),),
    )
    seen: set[int] = set()
    for raw_id in user_ids:
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if user_id in seen:
            continue
        seen.add(user_id)
        await db.execute(
            "INSERT INTO reporting_query_permissions (query_id, user_id) VALUES (%s, %s)",
            (int(query_id), user_id),
        )


async def user_has_permission(query_id: int, user_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT 1 AS allowed FROM reporting_query_permissions "
        "WHERE query_id = %s AND user_id = %s LIMIT 1",
        (int(query_id), int(user_id)),
    )
    return bool(row)


async def list_queries_for_user(
    user_id: int, *, include_all: bool = False
) -> list[dict[str, Any]]:
    """Return queries the given user is allowed to run.

    ``include_all=True`` (super admins) returns every query; otherwise only
    queries with a matching row in ``reporting_query_permissions`` are
    returned.
    """
    if include_all:
        return await list_queries()
    rows = await db.fetch_all(
        "SELECT q.id, q.slug, q.name, q.description, q.sql_query, q.is_system, "
        "q.created_by, q.created_at, q.updated_at "
        "FROM reporting_queries q "
        "JOIN reporting_query_permissions p ON p.query_id = q.id "
        "WHERE p.user_id = %s ORDER BY q.name ASC",
        (int(user_id),),
    )
    return [_normalise_query(row) for row in (rows or []) if row]
