from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.core.database import db


def _serialise_payload(payload: Mapping[str, Any] | Sequence[Any] | None) -> str:
    if payload is None:
        return json.dumps({})
    if isinstance(payload, (dict, list)):
        return json.dumps(payload)
    return json.dumps({"raw": payload})


def _deserialise_payload(value: Any) -> Mapping[str, Any] | Sequence[Any] | None:
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
    return {"raw": value}


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_bool(value: Any) -> bool:
    if value in (True, 1, "1", b"1"):
        return True
    if isinstance(value, (bytes, bytearray)):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return decoded == "1"
    return False


def _normalise_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _normalise_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_alert(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key in ("id", "monitor_id", "acknowledged_by"):
        if key in data and data[key] is not None:
            data[key] = int(data[key])
    data["importance"] = _normalise_bool(data.get("importance"))
    data["duration_seconds"] = _normalise_float(data.get("duration_seconds"))
    data["ping_ms"] = _normalise_float(data.get("ping_ms"))
    for field in ("occurred_at", "received_at", "acknowledged_at"):
        if data.get(field):
            data[field] = _make_aware(data[field])
    data["payload"] = _deserialise_payload(data.get("payload")) or {}
    return data


async def create_alert(
    *,
    event_uuid: str | None,
    monitor_id: int | None,
    monitor_name: str | None,
    monitor_url: str | None,
    monitor_type: str | None,
    monitor_hostname: str | None,
    monitor_port: str | None,
    status: str,
    previous_status: str | None,
    importance: bool,
    alert_type: str | None,
    reason: str | None,
    message: str | None,
    duration_seconds: float | None,
    ping_ms: float | None,
    occurred_at: datetime | None,
    remote_addr: str | None,
    user_agent: str | None,
    payload: Mapping[str, Any] | Sequence[Any] | None,
) -> dict[str, Any]:
    normalised_occurred_at = None
    if occurred_at is not None:
        if occurred_at.tzinfo is None:
            normalised_occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        else:
            normalised_occurred_at = occurred_at.astimezone(timezone.utc)
        normalised_occurred_at = normalised_occurred_at.replace(tzinfo=None)

    params = {
        "event_uuid": event_uuid,
        "monitor_id": monitor_id,
        "monitor_name": monitor_name,
        "monitor_url": monitor_url,
        "monitor_type": monitor_type,
        "monitor_hostname": monitor_hostname,
        "monitor_port": monitor_port,
        "status": status,
        "previous_status": previous_status,
        "importance": 1 if importance else 0,
        "alert_type": alert_type,
        "reason": reason,
        "message": message,
        "duration_seconds": duration_seconds,
        "ping_ms": ping_ms,
        "occurred_at": normalised_occurred_at,
        "remote_addr": remote_addr,
        "user_agent": user_agent,
        "payload": _serialise_payload(payload),
    }

    alert_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO uptimekuma_alerts (
            event_uuid,
            monitor_id,
            monitor_name,
            monitor_url,
            monitor_type,
            monitor_hostname,
            monitor_port,
            status,
            previous_status,
            importance,
            alert_type,
            reason,
            message,
            duration_seconds,
            ping_ms,
            occurred_at,
            remote_addr,
            user_agent,
            payload
        ) VALUES (
            %(event_uuid)s,
            %(monitor_id)s,
            %(monitor_name)s,
            %(monitor_url)s,
            %(monitor_type)s,
            %(monitor_hostname)s,
            %(monitor_port)s,
            %(status)s,
            %(previous_status)s,
            %(importance)s,
            %(alert_type)s,
            %(reason)s,
            %(message)s,
            %(duration_seconds)s,
            %(ping_ms)s,
            %(occurred_at)s,
            %(remote_addr)s,
            %(user_agent)s,
            %(payload)s
        )
        """,
        params,
    )
    if not alert_id:
        raise RuntimeError("Failed to insert Uptime Kuma alert")
    record = await get_alert(alert_id)
    if not record:
        raise RuntimeError("Unable to fetch persisted Uptime Kuma alert")
    return record


async def get_alert(alert_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM uptimekuma_alerts
        WHERE id = %s
        LIMIT 1
        """,
        (alert_id,),
    )
    return _normalise_alert(row) if row else None


def _build_filters(
    *,
    status: str | None,
    monitor_id: int | None,
    importance: bool | None,
    search: str | None,
) -> tuple[list[str], list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []

    if status:
        clauses.append("status = %s")
        params.append(status)

    if monitor_id is not None:
        clauses.append("monitor_id = %s")
        params.append(monitor_id)

    if importance is not None:
        clauses.append("importance = %s")
        params.append(1 if importance else 0)

    if search:
        like = f"%{search.lower()}%"
        clauses.append(
            "(LOWER(COALESCE(message, '')) LIKE %s OR LOWER(COALESCE(reason, '')) LIKE %s"
            " OR LOWER(COALESCE(monitor_name, '')) LIKE %s)"
        )
        params.extend([like, like, like])

    return clauses, params


async def list_alerts(
    *,
    status: str | None = None,
    monitor_id: int | None = None,
    importance: bool | None = None,
    search: str | None = None,
    sort_by: str = "received_at",
    sort_direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses, params = _build_filters(
        status=status,
        monitor_id=monitor_id,
        importance=importance,
        search=search,
    )

    sort_columns = {
        "received_at": "received_at",
        "occurred_at": "occurred_at",
        "monitor_name": "monitor_name",
        "status": "status",
    }
    column = sort_columns.get(sort_by, "received_at")
    order = "ASC" if sort_direction.lower() == "asc" else "DESC"

    params.extend([limit, offset])
    rows = await db.fetch_all(
        f"""
        SELECT *
        FROM uptimekuma_alerts
        WHERE {' AND '.join(clauses)}
        ORDER BY {column} {order}, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [_normalise_alert(row) for row in rows]
