from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from typing import Any

PermissionMapping = Sequence[dict[str, Any]]

from app.core.database import db
from app.security.api_keys import GeneratedApiKey, generate_api_key, hash_api_key


def _to_utc(dt: datetime | str | None) -> datetime | None:
    """Normalise database datetime values to timezone-aware UTC datetimes."""

    if dt is None:
        return None

    if isinstance(dt, str):
        value = dt.strip()
        if not value:
            return None
        # Support common database string formats (e.g. MySQL "YYYY-mm-dd HH:MM:SS")
        # and ISO-8601 with "Z" suffixes by normalising to Python's ISO format.
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"Unable to parse datetime value '{dt}'") from exc

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def create_api_key(
    *,
    description: str | None,
    expiry_date: date | None,
    permissions: PermissionMapping | None = None,
) -> tuple[str, dict[str, Any]]:
    generated: GeneratedApiKey = generate_api_key()
    await db.execute(
        """
        INSERT INTO api_keys (api_key, key_prefix, description, expiry_date, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            generated.hashed,
            generated.prefix,
            description,
            expiry_date,
            datetime.utcnow(),
        ),
    )
    row = await db.fetch_one(
        "SELECT * FROM api_keys WHERE api_key = %s",
        (generated.hashed,),
    )
    if not row:
        raise RuntimeError("Failed to fetch inserted API key record")
    row["created_at"] = _to_utc(row.get("created_at"))
    row["last_used_at"] = _to_utc(row.get("last_used_at"))
    stored_permissions = await _replace_api_key_permissions(row["id"], permissions or [])
    row["permissions"] = stored_permissions
    return generated.value, row


async def list_api_keys_with_usage(
    *,
    search: str | None = None,
    include_expired: bool = False,
    order_by: str = "created_at",
    order_direction: str = "desc",
) -> list[dict[str, Any]]:
    valid_order_columns = {
        "created_at": "ak.created_at",
        "description": "ak.description",
        "expiry_date": "ak.expiry_date",
        "usage_count": "usage_count",
        "last_used_at": "last_seen_at",
    }
    column = valid_order_columns.get(order_by, "ak.created_at")
    direction = "ASC" if order_direction.lower() == "asc" else "DESC"
    clauses: list[str] = []
    params: list[Any] = []
    if search:
        clauses.append("(ak.description LIKE %s OR ak.key_prefix LIKE %s)")
        like = f"%{search}%"
        params.extend([like, like])
    if not include_expired:
        clauses.append("(ak.expiry_date IS NULL OR ak.expiry_date >= CURRENT_DATE())")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT
            ak.id,
            ak.description,
            ak.expiry_date,
            ak.created_at,
            ak.last_used_at,
            ak.key_prefix,
            COALESCE(SUM(aku.usage_count), 0) AS usage_count,
            GREATEST(
                COALESCE(MAX(aku.last_used_at), '1970-01-01 00:00:00'),
                COALESCE(ak.last_used_at, '1970-01-01 00:00:00')
            ) AS last_seen_at
        FROM api_keys AS ak
        LEFT JOIN api_key_usage AS aku ON ak.id = aku.api_key_id
        {where}
        GROUP BY
            ak.id,
            ak.description,
            ak.expiry_date,
            ak.created_at,
            ak.last_used_at,
            ak.key_prefix
        ORDER BY {column} {direction}, ak.id ASC
    """
    rows = await db.fetch_all(sql, tuple(params))
    key_ids = [row["id"] for row in rows]
    usage_map = await _fetch_usage_by_key(key_ids)
    permission_map = await _fetch_permissions_by_key(key_ids)
    normalised: list[dict[str, Any]] = []
    for row in rows:
        info = dict(row)
        # Normalise numeric aggregation results that may come back as Decimals.
        usage_count = info.get("usage_count")
        info["usage_count"] = int(usage_count or 0)
        info["created_at"] = _to_utc(info.get("created_at"))
        info["last_used_at"] = _to_utc(info.get("last_used_at"))
        info["last_seen_at"] = _to_utc(info.get("last_seen_at"))
        info["usage"] = usage_map.get(row["id"], [])
        info["permissions"] = permission_map.get(row["id"], [])
        normalised.append(info)
    return normalised


async def get_api_key_with_usage(api_key_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT
            ak.id,
            ak.description,
            ak.expiry_date,
            ak.created_at,
            ak.last_used_at,
            ak.key_prefix,
            COALESCE(SUM(aku.usage_count), 0) AS usage_count,
            GREATEST(
                COALESCE(MAX(aku.last_used_at), '1970-01-01 00:00:00'),
                COALESCE(ak.last_used_at, '1970-01-01 00:00:00')
            ) AS last_seen_at
        FROM api_keys AS ak
        LEFT JOIN api_key_usage AS aku ON ak.id = aku.api_key_id
        WHERE ak.id = %s
        GROUP BY
            ak.id,
            ak.description,
            ak.expiry_date,
            ak.created_at,
            ak.last_used_at,
            ak.key_prefix
        """,
        (api_key_id,),
    )
    if not row:
        return None
    usage_map = await _fetch_usage_by_key([api_key_id])
    permission_map = await _fetch_permissions_by_key([api_key_id])
    info = dict(row)
    usage_count = info.get("usage_count")
    info["usage_count"] = int(usage_count or 0)
    info["created_at"] = _to_utc(info.get("created_at"))
    info["last_used_at"] = _to_utc(info.get("last_used_at"))
    info["last_seen_at"] = _to_utc(info.get("last_seen_at"))
    info["usage"] = usage_map.get(api_key_id, [])
    info["permissions"] = permission_map.get(api_key_id, [])
    return info


async def delete_api_key(api_key_id: int) -> None:
    await db.execute("DELETE FROM api_keys WHERE id = %s", (api_key_id,))


async def update_api_key_expiry(api_key_id: int, expiry_date: date | None) -> None:
    await db.execute(
        "UPDATE api_keys SET expiry_date = %s WHERE id = %s",
        (expiry_date, api_key_id),
    )


async def get_api_key_record(api_key_value: str) -> dict[str, Any] | None:
    hashed = hash_api_key(api_key_value)
    row = await db.fetch_one(
        """
        SELECT *
        FROM api_keys
        WHERE api_key = %s
          AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE())
        """,
        (hashed,),
    )
    if not row:
        return None
    row["created_at"] = _to_utc(row.get("created_at"))
    row["last_used_at"] = _to_utc(row.get("last_used_at"))
    permission_map = await _fetch_permissions_by_key([row["id"]])
    row["permissions"] = permission_map.get(row["id"], [])
    return row


async def record_api_key_usage(api_key_id: int, ip_address: str) -> None:
    await db.execute(
        """
        INSERT INTO api_key_usage (api_key_id, ip_address, usage_count, last_used_at)
        VALUES (%s, %s, 1, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE usage_count = usage_count + 1, last_used_at = UTC_TIMESTAMP()
        """,
        (api_key_id, ip_address),
    )
    await db.execute(
        "UPDATE api_keys SET last_used_at = UTC_TIMESTAMP() WHERE id = %s",
        (api_key_id,),
    )


async def _fetch_usage_by_key(key_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    ids = list(key_ids)
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = await db.fetch_all(
        f"""
        SELECT api_key_id, ip_address, usage_count, last_used_at
        FROM api_key_usage
        WHERE api_key_id IN ({placeholders})
        ORDER BY usage_count DESC
        """,
        tuple(ids),
    )
    usage: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        key_id = row["api_key_id"]
        usage.setdefault(key_id, []).append(
            {
                "ip_address": row["ip_address"],
                "usage_count": int(row.get("usage_count") or 0),
                "last_used_at": _to_utc(row.get("last_used_at")),
            }
        )
    return usage


async def _fetch_permissions_by_key(key_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    ids = list(key_ids)
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = await db.fetch_all(
        f"""
        SELECT api_key_id, route, method
        FROM api_key_endpoint_permissions
        WHERE api_key_id IN ({placeholders})
        ORDER BY route ASC, method ASC
        """,
        tuple(ids),
    )
    permissions: dict[int, dict[str, set[str]]] = {}
    for row in rows:
        key_id = row["api_key_id"]
        route = str(row["route"])
        method = str(row["method"]).upper()
        route_methods = permissions.setdefault(key_id, {}).setdefault(route, set())
        route_methods.add(method)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for key_id, route_map in permissions.items():
        grouped[key_id] = [
            {"path": route, "methods": sorted(methods)}
            for route, methods in sorted(route_map.items(), key=lambda item: item[0])
        ]
    return grouped


async def _replace_api_key_permissions(
    api_key_id: int, permissions: PermissionMapping
) -> list[dict[str, Any]]:
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM api_key_endpoint_permissions WHERE api_key_id = %s",
                (api_key_id,),
            )
            values: list[tuple[int, str, str]] = []
            for entry in permissions:
                path = str(entry.get("path", "")).strip()
                methods = entry.get("methods") or []
                if not path or not methods:
                    continue
                for method in methods:
                    values.append((api_key_id, path, str(method).upper()))
            if values:
                await cursor.executemany(
                    """
                    INSERT INTO api_key_endpoint_permissions (api_key_id, route, method)
                    VALUES (%s, %s, %s)
                    """,
                    values,
                )
    permission_map = await _fetch_permissions_by_key([api_key_id])
    return permission_map.get(api_key_id, [])
