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


_AI_LOOKUP_INT_FIELDS = {
    "ai_lookup_frequency_operational",
    "ai_lookup_frequency_degraded",
    "ai_lookup_frequency_partial_outage",
    "ai_lookup_frequency_outage",
    "ai_lookup_frequency_maintenance",
}

_AI_LOOKUP_TEXT_FIELDS = {
    "ai_lookup_url",
    "ai_lookup_prompt",
    "ai_lookup_model_override",
}

_AI_LOOKUP_DEFAULTS = {
    "ai_lookup_enabled": 0,
    "ai_lookup_url": None,
    "ai_lookup_prompt": None,
    "ai_lookup_model_override": None,
    "ai_lookup_frequency_operational": 60,
    "ai_lookup_frequency_degraded": 15,
    "ai_lookup_frequency_partial_outage": 10,
    "ai_lookup_frequency_outage": 5,
    "ai_lookup_frequency_maintenance": 60,
    "ai_lookup_last_checked_at": None,
    "ai_lookup_last_status": None,
    "ai_lookup_last_message": None,
}


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
    # Parse tags from comma-separated string to list
    tags_value = normalised.get("tags")
    if tags_value and isinstance(tags_value, str):
        normalised["tags"] = [tag.strip() for tag in tags_value.split(",") if tag.strip()]
    else:
        normalised["tags"] = []
    # Normalise AI lookup fields - use defaults for any missing columns (pre-migration)
    if "ai_lookup_enabled" in normalised:
        normalised["ai_lookup_enabled"] = bool(int(normalised["ai_lookup_enabled"] or 0))
    else:
        normalised["ai_lookup_enabled"] = False
    for field, default in _AI_LOOKUP_DEFAULTS.items():
        if field not in normalised:
            normalised[field] = default
    for field in _AI_LOOKUP_INT_FIELDS:
        if normalised.get(field) is not None:
            try:
                normalised[field] = int(normalised[field])
            except (TypeError, ValueError):
                normalised[field] = _AI_LOOKUP_DEFAULTS[field]
    for field in _AI_LOOKUP_TEXT_FIELDS:
        value = normalised.get(field)
        if isinstance(value, str):
            stripped = value.strip()
            normalised[field] = None if not stripped or stripped.lower() == "none" else stripped
    return normalised


_SELECT_COLUMNS = (
    "id, name, description, status, status_message, display_order, is_active, "
    "tags, created_at, updated_at, updated_by, "
    "ai_lookup_enabled, ai_lookup_url, ai_lookup_prompt, ai_lookup_model_override, "
    "ai_lookup_frequency_operational, ai_lookup_frequency_degraded, "
    "ai_lookup_frequency_partial_outage, ai_lookup_frequency_outage, "
    "ai_lookup_frequency_maintenance, "
    "ai_lookup_last_checked_at, ai_lookup_last_status, ai_lookup_last_message"
)


async def list_services(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if not include_inactive:
        filters.append("is_active = 1")
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = await db.fetch_all(
        f"""
        SELECT {_SELECT_COLUMNS}
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
        f"""
        SELECT {_SELECT_COLUMNS}
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
    service_id = await db.execute_returning_lastrowid(
        f"INSERT INTO service_status_services ({columns}) VALUES ({placeholders})",
        tuple(payload.values()),
    )
    if not service_id:
        raise RuntimeError("Failed to create service status entry")
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


async def find_service_by_name(name: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM service_status_services
        WHERE LOWER(name) = LOWER(%s) AND is_active = 1
        LIMIT 1
        """,
        (name,),
    )
    if not row:
        return None
    service_id = row.get("id")
    assignments = await _load_company_assignments([int(service_id)] if service_id else [])
    return _normalise_service(row, assignments)

async def delete_service(service_id: int) -> None:
    await db.execute("DELETE FROM service_status_services WHERE id = %s", (service_id,))


async def list_services_due_for_ai_lookup() -> list[dict[str, Any]]:
    """Return active services with AI lookup enabled that are due for a check."""
    rows = await db.fetch_all(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM service_status_services
        WHERE is_active = 1 AND ai_lookup_enabled = 1
        ORDER BY ai_lookup_last_checked_at ASC, id ASC
        """,
        (),
    )
    service_ids = [row.get("id") for row in rows if row.get("id") is not None]
    assignments = await _load_company_assignments([int(sid) for sid in service_ids])
    return [_normalise_service(row, assignments) for row in rows]


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
