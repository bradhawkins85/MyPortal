from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


async def list_tag_exclusions() -> list[dict[str, Any]]:
    """Retrieve all tag exclusions."""
    query = """
        SELECT id, tag_slug, created_at, created_by
        FROM tag_exclusions
        ORDER BY tag_slug ASC
    """
    async with db.cursor() as cursor:
        await cursor.execute(query)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_excluded_tag_slugs() -> set[str]:
    """Retrieve all excluded tag slugs as a set."""
    query = """
        SELECT tag_slug
        FROM tag_exclusions
    """
    async with db.cursor() as cursor:
        await cursor.execute(query)
        rows = await cursor.fetchall()
        return {row["tag_slug"] for row in rows}


async def add_tag_exclusion(tag_slug: str, created_by: int | None = None) -> dict[str, Any] | None:
    """Add a new tag exclusion."""
    query = """
        INSERT INTO tag_exclusions (tag_slug, created_by, created_at)
        VALUES (%s, %s, %s)
    """
    created_at = datetime.now(timezone.utc)
    async with db.cursor() as cursor:
        try:
            await cursor.execute(query, (tag_slug, created_by, created_at))
            await db.commit()
            exclusion_id = cursor.lastrowid
            return {
                "id": exclusion_id,
                "tag_slug": tag_slug,
                "created_at": created_at,
                "created_by": created_by,
            }
        except Exception:
            await db.rollback()
            return None


async def delete_tag_exclusion(tag_slug: str) -> bool:
    """Delete a tag exclusion by slug."""
    query = """
        DELETE FROM tag_exclusions
        WHERE tag_slug = %s
    """
    async with db.cursor() as cursor:
        await cursor.execute(query, (tag_slug,))
        affected = cursor.rowcount
        await db.commit()
        return affected > 0


async def is_tag_excluded(tag_slug: str) -> bool:
    """Check if a tag slug is in the exclusion list."""
    query = """
        SELECT COUNT(*) as count
        FROM tag_exclusions
        WHERE tag_slug = %s
    """
    async with db.cursor() as cursor:
        await cursor.execute(query, (tag_slug,))
        result = await cursor.fetchone()
        return result["count"] > 0 if result else False
