"""Asset table columns that admins have hidden from end users."""

from __future__ import annotations

from typing import Iterable

from app.core.database import db


async def list_restricted_keys() -> set[str]:
    """Return the keys of asset table columns hidden from end users."""
    rows = await db.fetch_all("SELECT column_key FROM asset_column_restrictions")
    return {str(row["column_key"]) for row in rows or [] if row.get("column_key")}


async def replace_restricted_keys(keys: Iterable[str]) -> None:
    """Replace the hidden column list with ``keys``."""
    await db.execute("DELETE FROM asset_column_restrictions")
    for key in sorted({str(key) for key in keys if key}):
        await db.execute(
            "INSERT INTO asset_column_restrictions (column_key) VALUES (%s)", (key,)
        )
