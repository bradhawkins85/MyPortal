from __future__ import annotations

from typing import Any

from app.core.database import db


async def get_settings() -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT discord_webhook_url FROM shop_settings WHERE id = 1"
    )
    return {
        "discord_webhook_url": row.get("discord_webhook_url") if row else None,
    }
