from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from app.core.database import db


async def list_company_assets(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            id,
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
            syncro_asset_id,
            tactical_asset_id
        FROM assets
        WHERE company_id = %s
        ORDER BY name ASC, id ASC
        """,
        (company_id,)
    )
    return list(rows or [])


async def get_asset_by_id(asset_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM assets WHERE id = %s",
        (asset_id,),
    )


async def delete_asset(asset_id: int) -> None:
    await db.execute("DELETE FROM assets WHERE id = %s", (asset_id,))


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
    tactical_asset_id: str | None = None,
    match_name: bool = False,
) -> None:
    sync_id = str(syncro_asset_id) if syncro_asset_id else None
    tactical_id = str(tactical_asset_id) if tactical_asset_id else None
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
    if not row and tactical_id:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND tactical_asset_id = %s",
            (company_id, tactical_id),
        )
    if not row and serial_number:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND serial_number = %s",
            (company_id, serial_number),
        )
    if not row and match_name and name:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND LOWER(name) = LOWER(%s)",
            (company_id, name),
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
        tactical_id,
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
                tactical_asset_id = %s,
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
                syncro_asset_id,
                tactical_asset_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                tactical_id,
            ),
        )


async def count_active_assets(*, company_id: Any = None, since: Any = None) -> int:
    """Return the number of assets that have synced since the provided date."""

    filters: list[str] = ["1 = 1"]
    params: list[Any] = []

    def _coerce_company_id(value: Any) -> int | None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate < 0:
            return None
        return candidate

    company_value = _coerce_company_id(company_id)
    if company_value is not None:
        filters.append("company_id = %s")
        params.append(company_value)

    since_dt = _ensure_datetime(since)
    if since_dt:
        filters.append("last_sync IS NOT NULL")
        filters.append("last_sync >= %s")
        params.append(since_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"))

    sql = f"SELECT COUNT(*) AS total FROM assets WHERE {' AND '.join(filters)}"
    row = await db.fetch_one(sql, tuple(params) if params else None)
    if not row:
        return 0
    try:
        return int(row.get("total") or 0)
    except (TypeError, ValueError):
        return 0


async def count_active_assets_by_type(
    *,
    company_id: Any = None,
    since: Any = None,
    device_type: str | None = None,
) -> int:
    """Return the number of assets of a specific type that have synced since the provided date.
    
    Args:
        company_id: Company ID to filter by
        since: Only count assets that synced since this datetime
        device_type: Device type to filter by (e.g., 'Workstation', 'Server', 'User')
    
    Returns:
        Count of matching assets
    """
    filters: list[str] = ["1 = 1"]
    params: list[Any] = []

    def _coerce_company_id(value: Any) -> int | None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate < 0:
            return None
        return candidate

    company_value = _coerce_company_id(company_id)
    if company_value is not None:
        filters.append("company_id = %s")
        params.append(company_value)

    since_dt = _ensure_datetime(since)
    if since_dt:
        filters.append("last_sync IS NOT NULL")
        filters.append("last_sync >= %s")
        params.append(since_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"))

    if device_type:
        filters.append("LOWER(type) = LOWER(%s)")
        params.append(device_type)

    sql = f"SELECT COUNT(*) AS total FROM assets WHERE {' AND '.join(filters)}"
    row = await db.fetch_one(sql, tuple(params) if params else None)
    if not row:
        return 0
    try:
        return int(row.get("total") or 0)
    except (TypeError, ValueError):
        return 0
