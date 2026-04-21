from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.core.database import db


async def create_audit_log(
    *,
    user_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    previous_value: Any = None,
    new_value: Any = None,
    metadata: dict[str, Any] | None = None,
    api_key: str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO audit_logs (user_id, action, entity_type, entity_id, previous_value, new_value, metadata, api_key, ip_address, request_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            action,
            entity_type,
            entity_id,
            _serialise(previous_value),
            _serialise(new_value),
            _serialise(metadata or {}),
            api_key,
            ip_address,
            request_id,
            datetime.utcnow(),
        ),
    )


async def list_audit_logs(
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    action: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if entity_type:
        clauses.append("al.entity_type = %s")
        params.append(entity_type)
    if entity_id is not None:
        clauses.append("al.entity_id = %s")
        params.append(entity_id)
    if user_id is not None:
        clauses.append("al.user_id = %s")
        params.append(user_id)
    if action:
        clauses.append("al.action LIKE %s")
        params.append(f"%{action}%")
    if request_id:
        clauses.append("al.request_id = %s")
        params.append(request_id)
    if ip_address:
        clauses.append("al.ip_address = %s")
        params.append(ip_address)
    if since is not None:
        clauses.append("al.created_at >= %s")
        params.append(since)
    if until is not None:
        clauses.append("al.created_at <= %s")
        params.append(until)
    if search:
        clauses.append(
            "(al.action LIKE %s OR al.entity_type LIKE %s OR u.email LIKE %s)"
        )
        like_value = f"%{search}%"
        params.extend([like_value, like_value, like_value])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    params.append(int(offset))
    rows = await db.fetch_all(
        f"""
        SELECT al.*, u.email AS user_email
        FROM audit_logs AS al
        LEFT JOIN users AS u ON u.id = al.user_id
        {where}
        ORDER BY al.created_at DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [_normalise(row) for row in rows]


async def count_audit_logs(
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    action: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if entity_type:
        clauses.append("al.entity_type = %s")
        params.append(entity_type)
    if entity_id is not None:
        clauses.append("al.entity_id = %s")
        params.append(entity_id)
    if user_id is not None:
        clauses.append("al.user_id = %s")
        params.append(user_id)
    if action:
        clauses.append("al.action LIKE %s")
        params.append(f"%{action}%")
    if request_id:
        clauses.append("al.request_id = %s")
        params.append(request_id)
    if ip_address:
        clauses.append("al.ip_address = %s")
        params.append(ip_address)
    if since is not None:
        clauses.append("al.created_at >= %s")
        params.append(since)
    if until is not None:
        clauses.append("al.created_at <= %s")
        params.append(until)
    if search:
        clauses.append(
            "(al.action LIKE %s OR al.entity_type LIKE %s OR u.email LIKE %s)"
        )
        like_value = f"%{search}%"
        params.extend([like_value, like_value, like_value])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    row = await db.fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM audit_logs AS al
        LEFT JOIN users AS u ON u.id = al.user_id
        {where}
        """,
        tuple(params),
    )
    if not row:
        return 0
    return int(row.get("total") or 0)


async def list_distinct_actions(limit: int = 200) -> list[str]:
    """Return the most recent distinct ``action`` values for filter UIs."""

    rows = await db.fetch_all(
        """
        SELECT action, MAX(created_at) AS last_seen
        FROM audit_logs
        GROUP BY action
        ORDER BY last_seen DESC
        LIMIT %s
        """,
        (int(limit),),
    )
    return [str(row.get("action")) for row in rows if row.get("action")]


async def prune_audit_logs(*, retention_days: int) -> int:
    """Delete audit_logs rows older than ``retention_days`` days.

    Returns the number of rows removed. Set ``retention_days`` to 0 (or less)
    to disable pruning - the function becomes a no-op in that case.
    """

    if retention_days is None or retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=int(retention_days))
    result = await db.execute(
        "DELETE FROM audit_logs WHERE created_at < %s",
        (cutoff,),
    )
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        return int(result.get("rowcount") or 0)
    return 0


async def get_audit_log(log_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT al.*, u.email AS user_email
        FROM audit_logs AS al
        LEFT JOIN users AS u ON u.id = al.user_id
        WHERE al.id = %s
        """,
        (log_id,),
    )
    if not row:
        return None
    return _normalise(row)


def _serialise(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return json.dumps(value)
    return json.dumps(value, default=str)


def _deserialise(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "previous_value": _deserialise(row.get("previous_value")),
        "new_value": _deserialise(row.get("new_value")),
        "metadata": _deserialise(row.get("metadata")),
        "created_at": row.get("created_at"),
    }
