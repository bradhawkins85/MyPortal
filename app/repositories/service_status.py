from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.core.database import db


async def _load_company_assignments(service_ids: Sequence[int]) -> dict[int, list[int]]:
    if not service_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(service_ids))
    rows = await db.fetch_all(
        f"""
        SELECT service_id, company_id
        FROM service_status_service_companies
        WHERE service_id IN ({placeholders})
        ORDER BY service_id, company_id
        """,
        tuple(int(service_id) for service_id in service_ids),
    )
    assignments: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        service_id = row.get("service_id")
        company_id = row.get("company_id")
        try:
            service_key = int(service_id)
            company_key = int(company_id)
        except (TypeError, ValueError):
            continue
        assignments[service_key].append(company_key)
    return assignments


def _normalise_service(row: dict[str, Any], assignments: dict[int, list[int]]) -> dict[str, Any]:
    normalised = dict(row)
    service_id = normalised.get("id")
    try:
        service_id_int = int(service_id)
    except (TypeError, ValueError):
        service_id_int = None
    else:
        normalised["id"] = service_id_int
    if "display_order" in normalised and normalised["display_order"] is not None:
        normalised["display_order"] = int(normalised["display_order"])
    if "is_active" in normalised and normalised["is_active"] is not None:
        normalised["is_active"] = int(normalised["is_active"]) == 1
    if "updated_by" in normalised and normalised["updated_by"] is not None:
        try:
            normalised["updated_by"] = int(normalised["updated_by"])
        except (TypeError, ValueError):
            normalised["updated_by"] = None
    if service_id_int is not None:
        normalised["company_ids"] = assignments.get(service_id_int, [])
    else:
        normalised["company_ids"] = []
    return normalised


async def list_services(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if not include_inactive:
        filters.append("is_active = 1")
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = await db.fetch_all(
        f"""
        SELECT id, name, description, status, status_message, display_order, is_active,
               created_at, updated_at, updated_by
        FROM service_status_services
        {where_clause}
        ORDER BY display_order ASC, name ASC
        """,
        tuple(params),
    )
    service_ids = [row.get("id") for row in rows if row.get("id") is not None]
    assignments = await _load_company_assignments([int(service_id) for service_id in service_ids])
    return [_normalise_service(row, assignments) for row in rows]


async def get_service(service_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT id, name, description, status, status_message, display_order, is_active,
               created_at, updated_at, updated_by
        FROM service_status_services
        WHERE id = %s
        """,
        (service_id,),
    )
    if not row:
        return None
    assignments = await _load_company_assignments([service_id])
    return _normalise_service(row, assignments)


async def create_service(payload: dict[str, Any], *, company_ids: Sequence[int] | None = None) -> dict[str, Any]:
    if not payload:
        raise ValueError("Missing payload for service creation")
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(["%s"] * len(payload))
    await db.execute(
        f"INSERT INTO service_status_services ({columns}) VALUES ({placeholders})",
        tuple(payload.values()),
    )
    row = await db.fetch_one("SELECT * FROM service_status_services WHERE id = LAST_INSERT_ID()")
    if not row:
        raise RuntimeError("Failed to create service status entry")
    service = _normalise_service(row, {})
    service_id = service.get("id")
    if service_id is None:
        raise RuntimeError("Created service is missing an identifier")
    await replace_service_companies(service_id, company_ids or [])
    return await get_service(service_id)


async def update_service(
    service_id: int,
    updates: dict[str, Any],
    *,
    company_ids: Sequence[int] | None = None,
) -> dict[str, Any]:
    if updates:
        assignments: list[str] = []
        params: list[Any] = []
        for column, value in updates.items():
            assignments.append(f"{column} = %s")
            params.append(value)
        params.append(service_id)
        sql = f"UPDATE service_status_services SET {', '.join(assignments)} WHERE id = %s"
        await db.execute(sql, tuple(params))
    if company_ids is not None:
        await replace_service_companies(service_id, company_ids)
    return await get_service(service_id)


async def delete_service(service_id: int) -> None:
    await db.execute("DELETE FROM service_status_services WHERE id = %s", (service_id,))


async def replace_service_companies(service_id: int, company_ids: Sequence[int]) -> None:
    unique_ids: list[int] = []
    seen: set[int] = set()
    for company_id in company_ids:
        try:
            company_int = int(company_id)
        except (TypeError, ValueError):
            continue
        if company_int <= 0 or company_int in seen:
            continue
        seen.add(company_int)
        unique_ids.append(company_int)
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM service_status_service_companies WHERE service_id = %s",
                (service_id,),
            )
            if unique_ids:
                values = [(service_id, company_id) for company_id in unique_ids]
                await cursor.executemany(
                    """
                    INSERT INTO service_status_service_companies (service_id, company_id)
                    VALUES (%s, %s)
                    """,
                    values,
                )
