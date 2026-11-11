"""Storage for processor launch dates and the device ages derived from them."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.database import db


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dt(value: datetime) -> str:
    return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _normalise(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["release_date"] = _to_date(record.get("release_date"))
    return record


async def list_asset_cpus() -> list[dict[str, Any]]:
    """Return every active asset with the CPU name it reports.

    The synced ``cpu_name`` is used first; assets with only a native RMM agent
    fall back to the processor the agent's inventory snapshot reported.
    """
    rows = await db.fetch_all(
        "SELECT a.id, a.cpu_name, a.cpu_release_date, a.approx_age, i.cpu_model "
        "FROM assets a LEFT JOIN rmm_device_inventory i ON i.asset_id = a.id "
        "WHERE a.archived_at IS NULL ORDER BY a.id ASC",
    )
    assets: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        asset_id = int(row["id"])
        record = assets.setdefault(asset_id, dict(row))
        if not str(record.get("cpu_model") or "").strip() and row.get("cpu_model"):
            record["cpu_model"] = row.get("cpu_model")
    for record in assets.values():
        record["cpu_release_date"] = _to_date(record.get("cpu_release_date"))
    return list(assets.values())


async def list_all() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, cpu_key, cpu_name, vendor, model, release_date, release_label, source, "
        "source_url, lookup_status, lookup_message, checked_at, created_at, updated_at "
        "FROM cpu_release_dates ORDER BY vendor ASC, model ASC, cpu_key ASC",
    )
    return [_normalise(row) for row in rows or []]


async def get(entry_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, cpu_key, cpu_name, vendor, model, release_date, release_label, source, "
        "source_url, lookup_status, lookup_message, checked_at, created_at, updated_at "
        "FROM cpu_release_dates WHERE id = %s",
        (int(entry_id),),
    )
    return _normalise(row) if row else None


async def get_by_key(cpu_key: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, cpu_key, cpu_name, vendor, model, release_date, release_label, source, "
        "source_url, lookup_status, lookup_message, checked_at "
        "FROM cpu_release_dates WHERE cpu_key = %s",
        (cpu_key,),
    )
    return _normalise(row) if row else None


async def ensure(cpu_key: str, cpu_name: str, vendor: str | None, model: str | None) -> None:
    """Add a processor model the first time it is seen."""
    await db.execute(
        "INSERT IGNORE INTO cpu_release_dates (cpu_key, cpu_name, vendor, model, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (cpu_key[:191], cpu_name[:255], vendor, (model or "")[:128] or None,
         _dt(_utcnow()), _dt(_utcnow())),
    )


async def list_due(*, retry_before: datetime, limit: int) -> list[dict[str, Any]]:
    """Return processors never checked, or not found/failed before ``retry_before``.

    Found and hand-entered dates are never looked up again.
    """
    rows = await db.fetch_all(
        "SELECT id, cpu_key, cpu_name, vendor, model, lookup_status, checked_at "
        "FROM cpu_release_dates "
        "WHERE (source IS NULL OR source <> 'manual') "
        "AND (lookup_status IS NULL OR (lookup_status <> 'found' AND (checked_at IS NULL OR checked_at < %s))) "
        "ORDER BY checked_at IS NULL DESC, checked_at ASC, id ASC LIMIT %s",
        (_dt(retry_before), int(limit)),
    )
    return [dict(row) for row in rows or []]


async def record_lookup(
    cpu_key: str,
    *,
    lookup_status: str,
    message: str | None,
    checked_at: datetime,
    source: str | None = None,
    release_date: date | None = None,
    release_label: str | None = None,
    source_url: str | None = None,
) -> None:
    """Store a lookup outcome; the date is only replaced when one was found."""
    note = (message or "")[:255] or None
    if lookup_status == "found":
        await db.execute(
            "UPDATE cpu_release_dates SET release_date = %s, release_label = %s, source = %s, "
            "source_url = %s, lookup_status = %s, lookup_message = %s, checked_at = %s, updated_at = %s "
            "WHERE cpu_key = %s AND (source IS NULL OR source <> 'manual')",
            (_d(release_date), (release_label or "")[:32] or None, source, (source_url or "")[:512] or None,
             lookup_status, note, _dt(checked_at), _dt(checked_at), cpu_key),
        )
        return
    await db.execute(
        "UPDATE cpu_release_dates SET lookup_status = %s, lookup_message = %s, checked_at = %s, "
        "updated_at = %s WHERE cpu_key = %s AND (source IS NULL OR source <> 'manual')",
        (lookup_status, note, _dt(checked_at), _dt(checked_at), cpu_key),
    )


async def set_manual(entry_id: int, *, release_date: date | None, release_label: str | None) -> None:
    """Record a launch date by hand, or clear it so the next run looks it up again."""
    now = _dt(_utcnow())
    if release_date:
        await db.execute(
            "UPDATE cpu_release_dates SET release_date = %s, release_label = %s, source = 'manual', "
            "source_url = NULL, lookup_status = 'found', lookup_message = %s, checked_at = %s, "
            "updated_at = %s WHERE id = %s",
            (_d(release_date), (release_label or "")[:32] or None, "Entered manually.", now, now, int(entry_id)),
        )
        return
    await db.execute(
        "UPDATE cpu_release_dates SET release_date = NULL, release_label = NULL, source = NULL, "
        "source_url = NULL, lookup_status = NULL, lookup_message = NULL, checked_at = NULL, "
        "updated_at = %s WHERE id = %s",
        (now, int(entry_id)),
    )


async def set_asset_cpu_age(asset_id: int, *, release_date: date | None, approx_age: int | None) -> None:
    """Store the CPU launch date on an asset and the approximate age derived from it."""
    if release_date is None:
        await db.execute(
            "UPDATE assets SET cpu_release_date = NULL WHERE id = %s",
            (int(asset_id),),
        )
        return
    await db.execute(
        "UPDATE assets SET cpu_release_date = %s, approx_age = %s WHERE id = %s",
        (_d(release_date), approx_age, int(asset_id)),
    )
