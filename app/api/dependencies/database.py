from __future__ import annotations

from app.core.database import db


async def require_database() -> None:
    if not db.is_connected():
        await db.connect()
    return None
