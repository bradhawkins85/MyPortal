"""Company-scoped role audiences for customer-published records."""

from __future__ import annotations

from typing import Iterable

from app.core.database import db

CONTENT_TYPES = frozenset({"knowledge_base", "asset"})


def _content_type(value: str) -> str:
    if value not in CONTENT_TYPES:
        raise ValueError("Unsupported customer content type")
    return value


async def list_role_ids(company_id: int, content_type: str, record_id: int) -> list[int]:
    rows = await db.fetch_all(
        "SELECT role_id FROM customer_content_role_audience WHERE company_id = %s AND content_type = %s AND record_id = %s ORDER BY role_id",
        (company_id, _content_type(content_type), record_id),
    )
    return [int(row["role_id"]) for row in rows]


async def list_role_ids_for_records(
    company_id: int, content_type: str, record_ids: Iterable[int]
) -> dict[int, list[int]]:
    """:func:`list_role_ids` for many records in one query; records without
    audience rows are left out."""

    ids = sorted({int(value) for value in record_ids})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    # Only "%s" placeholders are concatenated; every value is a bound parameter.
    rows = await db.fetch_all(
        "SELECT record_id, role_id FROM customer_content_role_audience "  # nosec B608
        f"WHERE company_id = %s AND content_type = %s AND record_id IN ({placeholders}) ORDER BY record_id, role_id",
        (company_id, _content_type(content_type), *ids),
    )
    roles: dict[int, list[int]] = {}
    for row in rows or []:
        roles.setdefault(int(row["record_id"]), []).append(int(row["role_id"]))
    return roles


async def role_can_access(company_id: int, content_type: str, record_id: int, role_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT 1 AS allowed FROM customer_content_role_audience WHERE company_id = %s AND content_type = %s AND record_id = %s AND role_id = %s",
        (company_id, _content_type(content_type), record_id, role_id),
    )
    return bool(row)


async def replace_roles(company_id: int, content_type: str, record_id: int, role_ids: Iterable[int]) -> None:
    kind = _content_type(content_type)
    await db.execute(
        "DELETE FROM customer_content_role_audience WHERE company_id = %s AND content_type = %s AND record_id = %s",
        (company_id, kind, record_id),
    )
    for role_id in sorted({int(value) for value in role_ids if int(value) > 0}):
        await db.execute(
            "INSERT INTO customer_content_role_audience (company_id, content_type, record_id, role_id) VALUES (%s, %s, %s, %s)",
            (company_id, kind, record_id, role_id),
        )
