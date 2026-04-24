"""Repository for per-company report section visibility preferences."""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.core.database import db


async def get_section_preferences(company_id: int) -> dict[str, bool]:
    """Return a ``{section_key: enabled}`` mapping for a company.

    Sections that have no row stored are treated as enabled by the caller.
    """
    rows = await db.fetch_all(
        "SELECT section_key, enabled FROM company_report_sections WHERE company_id = %s",
        (company_id,),
    )
    result: dict[str, bool] = {}
    for row in rows or []:
        key = row.get("section_key") if isinstance(row, Mapping) else row["section_key"]
        value = row.get("enabled") if isinstance(row, Mapping) else row["enabled"]
        if key is None:
            continue
        try:
            result[str(key)] = bool(int(value))
        except (TypeError, ValueError):
            result[str(key)] = bool(value)
    return result


async def get_detail_preferences(company_id: int) -> dict[str, bool]:
    """Return a ``{section_key: detailed}`` mapping for a company.

    Sections that have no row stored default to ``False`` (no detail page).
    """
    rows = await db.fetch_all(
        "SELECT section_key, detailed FROM company_report_sections WHERE company_id = %s",
        (company_id,),
    )
    result: dict[str, bool] = {}
    for row in rows or []:
        key = row.get("section_key") if isinstance(row, Mapping) else row["section_key"]
        value = row.get("detailed") if isinstance(row, Mapping) else row["detailed"]
        if key is None:
            continue
        try:
            result[str(key)] = bool(int(value))
        except (TypeError, ValueError):
            result[str(key)] = bool(value)
    return result


async def set_detail_preferences(
    company_id: int,
    preferences: Mapping[str, bool],
    *,
    valid_keys: Iterable[str] | None = None,
) -> None:
    """Persist the given detail preferences for a company.

    This updates only the ``detailed`` column of existing rows (preserving
    ``enabled``), inserting a new row with ``enabled=1`` when none exists.
    """
    allowed: set[str] | None = set(valid_keys) if valid_keys is not None else None
    for raw_key, detailed in preferences.items():
        key = str(raw_key)
        if allowed is not None and key not in allowed:
            continue
        # Read current enabled value so we don't clobber it.
        row = await db.fetch_one(
            "SELECT enabled FROM company_report_sections"
            " WHERE company_id = %s AND section_key = %s",
            (company_id, key),
        )
        if row is not None:
            enabled_val: int
            try:
                enabled_val = 1 if bool(int(
                    row.get("enabled") if isinstance(row, Mapping) else row["enabled"]
                )) else 0
            except (TypeError, ValueError):
                enabled_val = 1
        else:
            enabled_val = 1  # default — sections are enabled unless explicitly disabled
        await db.execute(
            "DELETE FROM company_report_sections"
            " WHERE company_id = %s AND section_key = %s",
            (company_id, key),
        )
        await db.execute(
            "INSERT INTO company_report_sections"
            " (company_id, section_key, enabled, detailed) VALUES (%s, %s, %s, %s)",
            (company_id, key, enabled_val, 1 if detailed else 0),
        )


async def set_section_preferences(
    company_id: int,
    preferences: Mapping[str, bool],
    *,
    valid_keys: Iterable[str] | None = None,
) -> None:
    """Persist the given section preferences for a company.

    Only keys present in ``valid_keys`` (when supplied) are written, which
    prevents callers from smuggling in arbitrary section identifiers.
    """
    allowed: set[str] | None = set(valid_keys) if valid_keys is not None else None
    for raw_key, enabled in preferences.items():
        key = str(raw_key)
        if allowed is not None and key not in allowed:
            continue
        # Emulate UPSERT so this works on both MySQL and SQLite.
        await db.execute(
            "DELETE FROM company_report_sections WHERE company_id = %s AND section_key = %s",
            (company_id, key),
        )
        await db.execute(
            "INSERT INTO company_report_sections (company_id, section_key, enabled) VALUES (%s, %s, %s)",
            (company_id, key, 1 if enabled else 0),
        )


async def delete_for_company(company_id: int) -> None:
    await db.execute(
        "DELETE FROM company_report_sections WHERE company_id = %s",
        (company_id,),
    )


async def get_company_report_settings(company_id: int) -> dict[str, Any]:
    """Return report-level settings for a company.

    Defaults: ``auto_hide_empty=True``, ``section_order=None`` (canonical order).
    """
    row = await db.fetch_one(
        "SELECT auto_hide_empty, section_order FROM company_report_settings WHERE company_id = %s",
        (company_id,),
    )
    if row is None:
        return {"auto_hide_empty": True, "section_order": None}
    raw_auto_hide = row.get("auto_hide_empty") if isinstance(row, Mapping) else row["auto_hide_empty"]
    raw_order = row.get("section_order") if isinstance(row, Mapping) else row["section_order"]
    try:
        auto_hide = bool(int(raw_auto_hide))
    except (TypeError, ValueError):
        auto_hide = bool(raw_auto_hide)
    section_order: list[str] | None = None
    if raw_order:
        try:
            section_order = json.loads(raw_order)
        except (json.JSONDecodeError, TypeError):
            section_order = None
    return {"auto_hide_empty": auto_hide, "section_order": section_order}


async def save_company_report_settings(
    company_id: int,
    auto_hide_empty: bool,
    section_order: list[str] | None,
) -> None:
    """Persist report-level settings for a company (upsert)."""
    auto_hide_val = 1 if auto_hide_empty else 0
    order_json = json.dumps(section_order) if section_order is not None else None
    # Emulate UPSERT for MySQL/SQLite compatibility.
    await db.execute(
        "DELETE FROM company_report_settings WHERE company_id = %s",
        (company_id,),
    )
    await db.execute(
        "INSERT INTO company_report_settings (company_id, auto_hide_empty, section_order)"
        " VALUES (%s, %s, %s)",
        (company_id, auto_hide_val, order_json),
    )


__all__ = [
    "get_section_preferences",
    "get_detail_preferences",
    "set_section_preferences",
    "set_detail_preferences",
    "delete_for_company",
    "get_company_report_settings",
    "save_company_report_settings",
]
