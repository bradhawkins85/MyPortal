"""Persistence for IEEE MAC address vendor assignments."""
from __future__ import annotations
from collections.abc import Iterable
from app.core.database import db

async def replace_all(assignments: Iterable[tuple[str, str]]) -> int:
    """Replace the current OUI list after a complete download has been parsed."""
    rows = list(assignments)
    await db.execute("DELETE FROM mac_vendors")
    for offset in range(0, len(rows), 500):
        batch = rows[offset : offset + 500]
        placeholders = ",".join(["(%s,%s)"] * len(batch))
        params = tuple(value for row in batch for value in row)
        await db.execute(f"INSERT INTO mac_vendors (oui_prefix, vendor) VALUES {placeholders}", params)
    return len(rows)
