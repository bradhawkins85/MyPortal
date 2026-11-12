"""Storage for Windows blue screen dumps reported by tray devices (:mod:`app.services.minidump_analysis`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.database import db

_COLUMNS = (
    "id, company_id, asset_id, tray_device_id, file_name, file_size, file_modified_at, crashed_at, "
    "bugcheck_code, bugcheck_name, suspect_driver, supported, summary_json, analysis_status, analysis_json, "
    "analysis_error, analysis_started_at, model, ticket_id, analysed_by_user_id, analysed_at, created_at, updated_at"
)
# Newest crash first; a dump with no usable crash time sorts by its file time.
_ORDER = " ORDER BY COALESCE(crashed_at, file_modified_at) DESC, id DESC"

STATUS_NONE = "none"
STATUS_ANALYSING = "analysing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    record["summary"] = _decode(record.pop("summary_json", None)) or {}
    record["analysis"] = _decode(record.pop("analysis_json", None))
    record["supported"] = bool(record.get("supported"))
    return record


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def resolve_asset_for_device(device: Mapping[str, Any]) -> dict[str, Any] | None:
    """The asset a tray device reports for, in the device's own company.

    The same rules the asset page uses to find an asset's tray device
    (:func:`app.repositories.ai_health_checks.find_tray_device_for_asset`),
    so a device the page shows also has its dumps stored: the linked asset,
    then the asset that names the device (``assets.tray_device_uid``), then
    the only live asset no other device has claimed with the device's serial
    number or host name. ``None`` when nothing, or more than one asset,
    matches.
    """

    company_id = _int(device.get("company_id"))
    if not company_id:
        return None
    columns = "SELECT id, company_id, name FROM assets WHERE company_id = %s AND "
    asset_id = _int(device.get("asset_id"))
    if asset_id:
        row = await db.fetch_one(columns + "id = %s", (company_id, asset_id))
        if row:
            return dict(row)
    device_uid = str(device.get("device_uid") or "").strip()
    if device_uid:
        row = await db.fetch_one(columns + "tray_device_uid = %s", (company_id, device_uid))
        if row:
            return dict(row)
    unclaimed = "archived_at IS NULL AND (tray_device_uid IS NULL OR tray_device_uid = '') AND "
    for column, value in (("serial_number", device.get("serial_number")), ("name", device.get("hostname"))):
        text = str(value or "").strip()
        if not text:
            continue
        rows = await db.fetch_all(
            columns + unclaimed + column + " IS NOT NULL AND LOWER(" + column + ") = LOWER(%s) LIMIT 2",
            (company_id, text),
        )
        if len(rows) == 1:
            return dict(rows[0])
        if rows:
            return None
    return None


async def upsert_dump(
    *, company_id: int, asset_id: int, tray_device_id: int, record: Mapping[str, Any]
) -> bool:
    """Store a reported dump; ``False`` when this device already reported the same file."""

    insert = "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"
    now = _utcnow()
    changed = await db.execute_rowcount(
        insert + " INTO asset_minidumps (company_id, asset_id, tray_device_id, file_name, file_size, "
        "file_modified_at, crashed_at, bugcheck_code, bugcheck_name, suspect_driver, supported, summary_json, "
        "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            company_id, asset_id, tray_device_id, record["file_name"], int(record.get("file_size") or 0),
            record["file_modified_at"], record.get("crashed_at"), record.get("bugcheck_code"),
            record.get("bugcheck_name") or None, record.get("suspect_driver") or None,
            1 if record.get("supported") else 0, json.dumps(record.get("summary") or {}), now, now,
        ),
    )
    return bool(changed)


async def prune(asset_id: int, keep: int = 50) -> int:
    """Delete the asset's oldest dumps beyond ``keep``; one being analysed is never deleted."""

    rows = await db.fetch_all(
        "SELECT id, analysis_status FROM asset_minidumps WHERE asset_id = %s" + _ORDER, (asset_id,)
    )
    doomed = [int(row["id"]) for row in rows[max(0, int(keep)):] if row["analysis_status"] != STATUS_ANALYSING]
    for start in range(0, len(doomed), 200):
        batch = doomed[start:start + 200]
        placeholders = ", ".join(["%s"] * len(batch))
        await db.execute(
            "DELETE FROM asset_minidumps WHERE asset_id = %s AND id IN (" + placeholders + ")",
            (asset_id, *batch),
        )
    return len(doomed)


async def list_for_asset(asset_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT " + _COLUMNS + " FROM asset_minidumps WHERE asset_id = %s" + _ORDER, (asset_id,)
    )
    return [record for record in (_row(row) for row in rows) if record]


async def count_for_asset(asset_id: int) -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS total FROM asset_minidumps WHERE asset_id = %s", (asset_id,))
    return int((row or {}).get("total") or 0)


async def get_dump(dump_id: int) -> dict[str, Any] | None:
    return _row(await db.fetch_one("SELECT " + _COLUMNS + " FROM asset_minidumps WHERE id = %s", (dump_id,)))


async def claim_analysis(
    dump_id: int, *, user_id: int | None, ticket_id: int | None, model: str,
    now: datetime, stale_before: datetime,
) -> bool:
    """Mark the dump as being analysed; ``False`` when another analysis already holds it.

    A single conditional update, so two technicians clicking at once cannot
    both start a run. A run that started before ``stale_before`` never
    finished and can be taken over. ``now`` identifies the run: pass it to
    :func:`finish_analysis` as ``started_at``.
    """

    changed = await db.execute_rowcount(
        "UPDATE asset_minidumps SET analysis_status = %s, analysis_started_at = %s, analysis_error = NULL, "
        "analysed_by_user_id = %s, ticket_id = %s, model = %s, updated_at = %s "
        "WHERE id = %s AND (analysis_status <> %s OR analysis_started_at IS NULL OR analysis_started_at < %s)",
        (STATUS_ANALYSING, now, user_id, ticket_id, model[:255], now, dump_id, STATUS_ANALYSING, stale_before),
    )
    return bool(changed)


async def finish_analysis(
    dump_id: int, *, status: str, analysis: Mapping[str, Any] | None, error: str | None,
    started_at: datetime, now: datetime | None = None,
) -> bool:
    """Record how the run that started at ``started_at`` ended.

    ``False``, changing nothing, when that run no longer holds the dump
    (another run took it over). A failure keeps the previous report.
    """

    now = now or _utcnow()
    owned = " WHERE id = %s AND analysis_status = %s AND analysis_started_at = %s"
    if analysis is None:
        changed = await db.execute_rowcount(
            "UPDATE asset_minidumps SET analysis_status = %s, analysis_error = %s, updated_at = %s" + owned,
            (status, (str(error)[:512] if error else None), now, dump_id, STATUS_ANALYSING, started_at),
        )
    else:
        changed = await db.execute_rowcount(
            "UPDATE asset_minidumps SET analysis_status = %s, analysis_json = %s, analysis_error = %s, "
            "analysed_at = %s, updated_at = %s" + owned,
            (status, json.dumps(analysis), (str(error)[:512] if error else None), now, now, dump_id,
             STATUS_ANALYSING, started_at),
        )
    return bool(changed)


async def update_analysis(dump_id: int, analysis: Mapping[str, Any]) -> None:
    """Replace a completed dump's stored report (to record how its ticket note went)."""

    await db.execute(
        "UPDATE asset_minidumps SET analysis_json = %s WHERE id = %s AND analysis_status = %s",
        (json.dumps(analysis), dump_id, STATUS_COMPLETED),
    )


async def fail_stale(asset_id: int, stale_before: datetime, *, error: str) -> int:
    """Fail the asset's analyses that started before ``stale_before`` and never finished."""

    return int(await db.execute_rowcount(
        "UPDATE asset_minidumps SET analysis_status = %s, analysis_error = %s, updated_at = %s "
        "WHERE asset_id = %s AND analysis_status = %s "
        "AND (analysis_started_at IS NULL OR analysis_started_at < %s)",
        (STATUS_FAILED, error[:512], _utcnow(), asset_id, STATUS_ANALYSING, stale_before),
    ) or 0)


async def asset_name(asset_id: int) -> str:
    row = await db.fetch_one("SELECT name FROM assets WHERE id = %s", (asset_id,))
    return str((row or {}).get("name") or "")
