"""Repository for per-company report section visibility preferences."""
from __future__ import annotations

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


__all__ = [
    "get_section_preferences",
    "set_section_preferences",
    "delete_for_company",
]
