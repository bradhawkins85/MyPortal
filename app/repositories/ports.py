from __future__ import annotations

from typing import Any, Iterable

from app.core.database import db

_VALID_ORDER_COLUMNS = {
    "name": "name",
    "country": "country",
    "updated_at": "updated_at",
}


def _normalise_ordering(order_by: str | None, direction: str | None) -> tuple[str, str]:
    column = _VALID_ORDER_COLUMNS.get((order_by or "").lower(), "name")
    order_direction = "DESC" if (direction or "").lower() == "desc" else "ASC"
    return column, order_direction


async def list_ports(
    *,
    search: str | None = None,
    country: str | None = None,
    active_only: bool = True,
    order_by: str | None = None,
    direction: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    if active_only:
        clauses.append("is_active = 1")
    if search:
        like = f"%{search.strip()}%"
        clauses.append("(name LIKE %s OR code LIKE %s OR country LIKE %s)")
        params.extend([like, like, like])
    if country:
        clauses.append("country = %s")
        params.append(country)

    column, order_direction = _normalise_ordering(order_by, direction)
    sql = (
        "SELECT id, name, code, country, region, timezone, description, latitude, longitude, "
        "is_active, created_at, updated_at "
        f"FROM ports WHERE {' AND '.join(clauses)} "
        f"ORDER BY {column} {order_direction} LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])
    rows = await db.fetch_all(sql, tuple(params))
    return list(rows)


async def get_port_by_id(port_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, name, code, country, region, timezone, description, latitude, longitude, "
        "is_active, created_at, updated_at FROM ports WHERE id = %s",
        (port_id,),
    )


async def get_port_by_code(code: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, name, code, country, region, timezone, description, latitude, longitude, "
        "is_active, created_at, updated_at FROM ports WHERE code = %s",
        (code,),
    )


async def create_port(**values: Any) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO ports (name, code, country, region, timezone, description, latitude, longitude, is_active)
        VALUES (%(name)s, %(code)s, %(country)s, %(region)s, %(timezone)s, %(description)s, %(latitude)s, %(longitude)s, %(is_active)s)
        """,
        values,
    )
    row = await get_port_by_code(values["code"])
    if not row:
        raise RuntimeError("Failed to persist port record")
    return row


async def update_port(port_id: int, **values: Any) -> dict[str, Any]:
    if not values:
        port = await get_port_by_id(port_id)
        if not port:
            raise ValueError("Port not found")
        return port
    assignments: list[str] = []
    params: list[Any] = []
    for column, value in values.items():
        assignments.append(f"{column} = %s")
        params.append(value)
    params.append(port_id)
    await db.execute(f"UPDATE ports SET {', '.join(assignments)} WHERE id = %s", tuple(params))
    updated = await get_port_by_id(port_id)
    if not updated:
        raise ValueError("Port not found after update")
    return updated


async def delete_port(port_id: int) -> None:
    await db.execute("DELETE FROM ports WHERE id = %s", (port_id,))


async def bulk_get_ports(port_ids: Iterable[int]) -> list[dict[str, Any]]:
    ids = list({int(pid) for pid in port_ids})
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    sql = (
        "SELECT id, name, code, country, region, timezone, description, latitude, longitude, is_active, created_at, updated_at "
        f"FROM ports WHERE id IN ({placeholders})"
    )
    rows = await db.fetch_all(sql, tuple(ids))
    return list(rows)
