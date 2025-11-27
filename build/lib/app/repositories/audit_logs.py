from __future__ import annotations

import json
from datetime import datetime
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
) -> None:
    await db.execute(
        """
        INSERT INTO audit_logs (user_id, action, entity_type, entity_id, previous_value, new_value, metadata, api_key, ip_address, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            datetime.utcnow(),
        ),
    )


async def list_audit_logs(
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    limit: int = 200,
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
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await db.fetch_all(
        f"""
        SELECT al.*, u.email AS user_email
        FROM audit_logs AS al
        LEFT JOIN users AS u ON u.id = al.user_id
        {where}
        ORDER BY al.created_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return [_normalise(row) for row in rows]


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
