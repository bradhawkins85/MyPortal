from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.database import db


def _utcnow() -> datetime:
    """Return a timezone-naive UTC timestamp for database writes."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalise aware datetimes to timezone-naive UTC values."""

    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _serialise(value: Any) -> str | None:
    if value is None:
        return None
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


def _make_aware(dt: Any) -> datetime | None:
    if not dt:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _normalise_event(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key in ("id", "attempt_count", "max_attempts", "backoff_seconds", "response_status"):
        if key in data and data[key] is not None:
            data[key] = int(data[key])
    data["headers"] = _deserialise(data.get("headers"))
    data["payload"] = _deserialise(data.get("payload"))
    if data.get("created_at"):
        data["created_at"] = _make_aware(data["created_at"])
    if data.get("updated_at"):
        data["updated_at"] = _make_aware(data["updated_at"])
    if data.get("next_attempt_at"):
        data["next_attempt_at"] = _make_aware(data["next_attempt_at"])
    return data


async def count_events_by_status(status: str) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM webhook_events WHERE status = %s",
        (status,),
    )
    return int(row["count"]) if row else 0


def _normalise_attempt(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key in ("id", "event_id", "attempt_number", "response_status"):
        if key in data and data[key] is not None:
            data[key] = int(data[key])
    data["request_headers"] = _deserialise(data.get("request_headers"))
    data["request_body"] = _deserialise(data.get("request_body"))
    data["response_headers"] = _deserialise(data.get("response_headers"))
    if data.get("attempted_at"):
        data["attempted_at"] = _make_aware(data["attempted_at"])
    return data


async def create_event(
    *,
    name: str,
    target_url: str,
    headers: dict[str, Any] | None = None,
    payload: Any = None,
    max_attempts: int = 3,
    backoff_seconds: int = 300,
) -> dict[str, Any]:
    event_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO webhook_events
            (name, target_url, headers, payload, max_attempts, backoff_seconds)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            target_url,
            _serialise(headers),
            _serialise(payload),
            max(1, max_attempts),
            max(1, backoff_seconds),
        ),
    )
    if not event_id:
        return {}
    row = await get_event(event_id)
    return row or {}


async def get_event(event_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM webhook_events WHERE id = %s",
        (event_id,),
    )
    return _normalise_event(row) if row else None


async def list_events(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE status = %s"
        params.append(status)
    params.append(limit)
    rows = await db.fetch_all(
        f"SELECT * FROM webhook_events {where} ORDER BY updated_at DESC LIMIT %s",
        tuple(params),
    )
    return [_normalise_event(row) for row in rows]


async def list_due_events(limit: int = 25) -> list[dict[str, Any]]:
    now = _utcnow()
    rows = await db.fetch_all(
        """
        SELECT *
        FROM webhook_events
        WHERE status = 'pending'
          AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
        ORDER BY created_at ASC
        LIMIT %s
        """,
        (now, limit),
    )
    return [_normalise_event(row) for row in rows]


async def list_stalled_events(timeout_seconds: int = 600) -> list[dict[str, Any]]:
    """Find webhook events stuck in 'in_progress' status beyond the timeout threshold."""
    now = _utcnow()
    cutoff = datetime(now.year, now.month, now.day, now.hour, now.minute, now.second) - timedelta(seconds=timeout_seconds)
    rows = await db.fetch_all(
        """
        SELECT *
        FROM webhook_events
        WHERE status = 'in_progress'
          AND updated_at < %s
        ORDER BY updated_at ASC
        """,
        (cutoff,),
    )
    return [_normalise_event(row) for row in rows]


async def delete_event(event_id: int) -> None:
    await db.execute(
        "DELETE FROM webhook_events WHERE id = %s",
        (event_id,),
    )


async def delete_events_by_status(status: str) -> int:
    """Delete webhook events matching the provided status and return the number removed."""

    normalised = (status or "").strip().lower()
    if normalised not in {"failed", "succeeded"}:
        raise ValueError("Unsupported status for bulk webhook deletion")

    row = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM webhook_events WHERE status = %s",
        (normalised,),
    )
    count = int(row["count"]) if row and row.get("count") is not None else 0
    if count:
        await db.execute(
            "DELETE FROM webhook_events WHERE status = %s",
            (normalised,),
        )
    return count


async def delete_succeeded_before(cutoff: datetime) -> int:
    threshold = _ensure_naive_utc(cutoff)
    if threshold is None:
        return 0
    row = await db.fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM webhook_events
        WHERE status = 'succeeded'
          AND COALESCE(updated_at, created_at) < %s
        """,
        (threshold,),
    )
    count = int(row["count"]) if row and row.get("count") is not None else 0
    if count:
        await db.execute(
            """
            DELETE FROM webhook_events
            WHERE status = 'succeeded'
              AND COALESCE(updated_at, created_at) < %s
            """,
            (threshold,),
        )
    return count


async def mark_in_progress(event_id: int) -> None:
    now = _utcnow()
    await db.execute(
        """
        UPDATE webhook_events
        SET status = 'in_progress', updated_at = %s
        WHERE id = %s AND status = 'pending'
        """,
        (now, event_id),
    )


async def record_attempt(
    *,
    event_id: int,
    attempt_number: int,
    status: str,
    response_status: int | None,
    response_body: str | None,
    error_message: str | None,
    request_headers: dict[str, Any] | None = None,
    request_body: Any = None,
    response_headers: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO webhook_event_attempts
            (
                event_id,
                attempt_number,
                status,
                response_status,
                response_body,
                error_message,
                request_headers,
                request_body,
                response_headers
            )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            attempt_number,
            status,
            response_status,
            response_body,
            error_message,
            _serialise(request_headers),
            _serialise(request_body),
            _serialise(response_headers),
        ),
    )


async def mark_event_completed(
    event_id: int,
    *,
    attempt_number: int,
    response_status: int | None,
    response_body: str | None,
) -> None:
    now = _utcnow()
    await db.execute(
        """
        UPDATE webhook_events
        SET status = 'succeeded',
            response_status = %s,
            response_body = %s,
            attempt_count = %s,
            last_error = NULL,
            next_attempt_at = NULL,
            updated_at = %s
        WHERE id = %s
        """,
        (
            response_status,
            response_body,
            attempt_number,
            now,
            event_id,
        ),
    )


async def mark_event_failed(
    event_id: int,
    *,
    attempt_number: int,
    error_message: str | None,
    response_status: int | None,
    response_body: str | None,
) -> None:
    now = _utcnow()
    await db.execute(
        """
        UPDATE webhook_events
        SET status = 'failed',
            response_status = %s,
            response_body = %s,
            attempt_count = %s,
            last_error = %s,
            next_attempt_at = NULL,
            updated_at = %s
        WHERE id = %s
        """,
        (
            response_status,
            response_body,
            attempt_number,
            error_message,
            now,
            event_id,
        ),
    )


async def schedule_retry(
    event_id: int,
    *,
    attempt_number: int,
    next_attempt_at: datetime,
    error_message: str | None,
    response_status: int | None,
    response_body: str | None,
) -> None:
    now = _utcnow()
    next_attempt = _ensure_naive_utc(next_attempt_at)
    await db.execute(
        """
        UPDATE webhook_events
        SET status = 'pending',
            attempt_count = %s,
            next_attempt_at = %s,
            last_error = %s,
            response_status = %s,
            response_body = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (
            attempt_number,
            next_attempt,
            error_message,
            response_status,
            response_body,
            now,
            event_id,
        ),
    )


async def list_attempts(event_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM webhook_event_attempts
        WHERE event_id = %s
        ORDER BY attempted_at DESC
        LIMIT %s
        """,
        (event_id, limit),
    )
    return [_normalise_attempt(row) for row in rows]


async def force_retry(event_id: int) -> None:
    now = _utcnow()
    await db.execute(
        """
        UPDATE webhook_events
        SET status = 'pending',
            next_attempt_at = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (now, now, event_id),
    )
