from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


WatchRecord = dict[str, Any]


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_watch(row: dict[str, Any]) -> WatchRecord:
    record = dict(row)
    for key in ("id", "ticket_id", "poll_interval_seconds"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    record["active"] = bool(int(record.get("active") or 0))
    record["public_comments_enabled"] = bool(int(record.get("public_comments_enabled") or 0))
    for key in ("created_at", "updated_at", "last_checked_at", "last_posted_update_at"):
        record[key] = _make_aware(record.get(key))
    snapshot_raw = record.get("last_snapshot_json")
    if isinstance(snapshot_raw, str) and snapshot_raw.strip():
        try:
            record["last_snapshot"] = json.loads(snapshot_raw)
        except json.JSONDecodeError:
            record["last_snapshot"] = None
    elif isinstance(snapshot_raw, dict):
        record["last_snapshot"] = snapshot_raw
    else:
        record["last_snapshot"] = None
    return record


async def get_watch_by_ticket(ticket_id: int) -> WatchRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM ticket_shipment_watches WHERE ticket_id = %s LIMIT 1",
        (ticket_id,),
    )
    return _normalise_watch(row) if row else None


async def get_watch_by_id(watch_id: int) -> WatchRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM ticket_shipment_watches WHERE id = %s LIMIT 1",
        (watch_id,),
    )
    return _normalise_watch(row) if row else None


async def upsert_watch(
    *,
    ticket_id: int,
    tracking_url: str,
    provider: str,
    consignment_id: str | None,
    poll_interval_seconds: int,
    active: bool,
    public_comments_enabled: bool = True,
) -> WatchRecord:
    existing = await get_watch_by_ticket(ticket_id)
    if existing:
        await db.execute(
            """
            UPDATE ticket_shipment_watches
            SET tracking_url = %s,
                provider = %s,
                consignment_id = %s,
                poll_interval_seconds = %s,
                active = %s,
                public_comments_enabled = %s
            WHERE ticket_id = %s
            """,
            (
                tracking_url,
                provider,
                consignment_id,
                max(60, int(poll_interval_seconds)),
                1 if active else 0,
                1 if public_comments_enabled else 0,
                ticket_id,
            ),
        )
    else:
        await db.execute(
            """
            INSERT INTO ticket_shipment_watches
                (ticket_id, tracking_url, provider, consignment_id, poll_interval_seconds, active, public_comments_enabled)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ticket_id,
                tracking_url,
                provider,
                consignment_id,
                max(60, int(poll_interval_seconds)),
                1 if active else 0,
                1 if public_comments_enabled else 0,
            ),
        )
    refreshed = await get_watch_by_ticket(ticket_id)
    if not refreshed:
        raise RuntimeError("Unable to persist ticket shipment watch")
    return refreshed


async def disable_watch(ticket_id: int) -> None:
    await db.execute(
        "UPDATE ticket_shipment_watches SET active = 0 WHERE ticket_id = %s",
        (ticket_id,),
    )


async def list_active_watches(*, limit: int = 200) -> list[WatchRecord]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM ticket_shipment_watches
        WHERE active = 1
        ORDER BY last_checked_at ASC, id ASC
        LIMIT %s
        """,
        (max(1, int(limit)),),
    )
    return [_normalise_watch(row) for row in rows]


async def update_watch_check_state(
    watch_id: int,
    *,
    last_checked_at: datetime,
    last_snapshot_hash: str | None = None,
    last_snapshot_json: dict[str, Any] | None = None,
    last_posted_update_at: datetime | None = None,
) -> None:
    assignments = ["last_checked_at = %s"]
    params: list[Any] = [last_checked_at.replace(tzinfo=None)]

    if last_snapshot_hash is not None:
        assignments.append("last_snapshot_hash = %s")
        params.append(last_snapshot_hash)
    if last_snapshot_json is not None:
        assignments.append("last_snapshot_json = %s")
        params.append(json.dumps(last_snapshot_json, ensure_ascii=False, sort_keys=True))
    if last_posted_update_at is not None:
        assignments.append("last_posted_update_at = %s")
        params.append(last_posted_update_at.replace(tzinfo=None))

    params.append(watch_id)
    # ``assignments`` is built from a fixed internal allowlist above (never from
    # user input), so joining the column fragment remains safe here.
    query = "UPDATE ticket_shipment_watches SET " + ", ".join(assignments) + " WHERE id = %s"
    await db.execute(
        query,
        tuple(params),
    )
