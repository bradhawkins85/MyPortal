"""Repository for plugin enable/disable state."""

from __future__ import annotations

from app.core.database import db


async def ensure_registered(slug: str) -> None:
    """Insert the plugin row if missing (idempotent)."""

    if not db.is_connected():
        return

    if db.is_sqlite():
        await db.execute(
            """
            INSERT OR IGNORE INTO plugin_registry (slug, enabled)
            VALUES (?, 1)
            """,
            (slug,),
        )
    else:
        await db.execute(
            """
            INSERT IGNORE INTO plugin_registry (slug, enabled)
            VALUES (%s, 1)
            """,
            (slug,),
        )


async def list_entries() -> list[dict]:
    if not db.is_connected():
        return []
    return await db.fetch_all(
        """
        SELECT slug, enabled, installed_at
        FROM plugin_registry
        ORDER BY slug
        """
    )


async def set_enabled(slug: str, enabled: bool) -> None:
    if not db.is_connected():
        return
    await ensure_registered(slug)
    await db.execute(
        """
        UPDATE plugin_registry
        SET enabled = ?
        WHERE slug = ?
        """,
        (1 if enabled else 0, slug),
    )


async def is_enabled(slug: str) -> bool:
    if not db.is_connected():
        return True
    row = await db.fetch_one(
        """
        SELECT enabled
        FROM plugin_registry
        WHERE slug = ?
        """,
        (slug,),
    )
    if not row:
        return True
    return bool(row.get("enabled"))
