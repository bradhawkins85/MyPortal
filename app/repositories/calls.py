from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

SUPPORTED_EVENTS: tuple[str, ...] = (
    "Incoming Call",
    "Outgoing Call",
    "Establish Call",
    "Terminate Call",
    "Off Hook",
    "On Hook",
    "Missed Call",
    "DND On",
    "DND Off",
    "Call Forwarding On",
    "Call Forwarding Off",
    "Hold Call",
    "Resume Call",
    "Syslog On",
    "Syslog Off",
    "Booting Completed",
    "Blind Transferring",
    "Attended Transferring",
    "Registration",
    "Sign Off",
)

SUPPORTED_VARIABLES: tuple[str, ...] = (
    "phone_ip",
    "mac",
    "product",
    "program_version",
    "hardware_version",
    "language",
    "local",
    "display_local",
    "remote",
    "display_remote",
    "call-id",
    "active_user",
    "active_host",
    "duration",
    "calldirection",
)

_SUPPORTED_VARIABLE_SET = frozenset(SUPPORTED_VARIABLES)
_SUPPORTED_EVENT_BY_KEY = {event.lower(): event for event in SUPPORTED_EVENTS}


async def _ensure_connection() -> None:
    is_connected = getattr(db, "is_connected", None)
    if callable(is_connected):
        try:
            if is_connected():
                return
        except Exception:  # pragma: no cover - defensive guard
            pass
    connect = getattr(db, "connect", None)
    if not connect:
        return
    result = connect()
    if hasattr(result, "__await__"):
        await result


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def normalise_event(value: str | None) -> str | None:
    if not value:
        return None
    return _SUPPORTED_EVENT_BY_KEY.get(value.strip().replace("_", " ").replace("-", " ").lower())


def filter_supported_params(params: Mapping[str, Any]) -> dict[str, str]:
    supported: dict[str, str] = {}
    for key, value in params.items():
        if key in _SUPPORTED_VARIABLE_SET:
            supported[key] = str(value)
    return supported


def _loads_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _normalise_record(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id",):
        if record.get(key) is not None:
            record[key] = int(record[key])
    for key in ("supported_params", "raw_params"):
        record[key] = _loads_json(record.get(key)) or {}
    record["received_at"] = _make_aware(record.get("received_at"))
    return record


async def create_call_event(
    *,
    webhook_token: str,
    event_name: str | None,
    supported_params: Mapping[str, Any],
    raw_params: Mapping[str, Any],
    source_ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    await _ensure_connection()
    remote_number = str(raw_params.get("number") or supported_params.get("remote") or "") or None
    local_number = str(supported_params.get("local") or "") or None
    call_id = str(supported_params.get("call-id") or "") or None
    duration_seconds = None
    try:
        if supported_params.get("duration") not in (None, ""):
            duration_seconds = int(str(supported_params.get("duration")))
    except (TypeError, ValueError):
        duration_seconds = None

    call_event_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO phone_call_events (
            webhook_token, event_name, remote_number, local_number, call_id,
            direction, duration_seconds, supported_params, raw_params,
            source_ip, user_agent
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            webhook_token,
            event_name,
            remote_number,
            local_number,
            call_id,
            supported_params.get("calldirection"),
            duration_seconds,
            json.dumps(dict(supported_params), sort_keys=True),
            json.dumps(dict(raw_params), sort_keys=True),
            source_ip,
            user_agent,
        ),
    )
    row = await db.fetch_one("SELECT * FROM phone_call_events WHERE id = %s", (call_event_id,))
    return _normalise_record(row) or {}


async def list_call_events(*, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    await _ensure_connection()
    rows = await db.fetch_all(
        """
        SELECT *
        FROM phone_call_events
        ORDER BY received_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
    return [record for row in rows if (record := _normalise_record(row))]
