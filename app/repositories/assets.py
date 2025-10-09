from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from app.core.database import db


def _ensure_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                dt = parsed
                break
            except ValueError:
                continue
        else:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _to_mysql_datetime(value: Any) -> str | None:
    dt = _ensure_datetime(value)
    if not dt:
        return None
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _to_mysql_date(value: Any) -> str | None:
    dt = _ensure_datetime(value)
    if not dt:
        return None
    return dt.date().isoformat()


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        digits = ""
        for ch in text:
            if ch.isdigit() or ch in {"-", "."}:
                digits += ch
            elif digits:
                break
        if not digits:
            return None
        try:
            return float(digits)
        except ValueError:
            return None


async def upsert_asset(
    *,
    company_id: int,
    name: str,
    type: str | None = None,
    serial_number: str | None = None,
    status: str | None = None,
    os_name: str | None = None,
    cpu_name: str | None = None,
    ram_gb: Any = None,
    hdd_size: str | None = None,
    last_sync: Any = None,
    motherboard_manufacturer: str | None = None,
    form_factor: str | None = None,
    last_user: str | None = None,
    approx_age: Any = None,
    performance_score: Any = None,
    warranty_status: str | None = None,
    warranty_end_date: Any = None,
    syncro_asset_id: str | None = None,
) -> None:
    sync_id = str(syncro_asset_id) if syncro_asset_id else None
    ram_value = _coerce_float(ram_gb)
    approx_value = _coerce_float(approx_age)
    performance_value = _coerce_float(performance_score)
    last_sync_db = _to_mysql_datetime(last_sync)
    warranty_end_db = _to_mysql_date(warranty_end_date)

    row = None
    if sync_id:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND syncro_asset_id = %s",
            (company_id, sync_id),
        )
    if not row and serial_number:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND serial_number = %s",
            (company_id, serial_number),
        )

    params = (
        name,
        type,
        status,
        os_name,
        cpu_name,
        ram_value,
        hdd_size,
        last_sync_db,
        motherboard_manufacturer,
        form_factor,
        last_user,
        approx_value,
        performance_value,
        warranty_status,
        warranty_end_db,
        sync_id,
        serial_number,
    )

    if row:
        await db.execute(
            """
            UPDATE assets
            SET name = %s,
                type = %s,
                status = %s,
                os_name = %s,
                cpu_name = %s,
                ram_gb = %s,
                hdd_size = %s,
                last_sync = %s,
                motherboard_manufacturer = %s,
                form_factor = %s,
                last_user = %s,
                approx_age = %s,
                performance_score = %s,
                warranty_status = %s,
                warranty_end_date = %s,
                syncro_asset_id = %s,
                serial_number = %s
            WHERE id = %s
            """,
            params + (row["id"],),
        )
    else:
        await db.execute(
            """
            INSERT INTO assets (
                company_id,
                name,
                type,
                serial_number,
                status,
                os_name,
                cpu_name,
                ram_gb,
                hdd_size,
                last_sync,
                motherboard_manufacturer,
                form_factor,
                last_user,
                approx_age,
                performance_score,
                warranty_status,
                warranty_end_date,
                syncro_asset_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                company_id,
                name,
                type,
                serial_number,
                status,
                os_name,
                cpu_name,
                ram_value,
                hdd_size,
                last_sync_db,
                motherboard_manufacturer,
                form_factor,
                last_user,
                approx_value,
                performance_value,
                warranty_status,
                warranty_end_db,
                sync_id,
            ),
        )
