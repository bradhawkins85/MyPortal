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
    return await db.fetch_all(query)


async def get_excluded_tag_slugs() -> set[str]:
    """Retrieve all excluded tag slugs as a set."""
    query = """
        SELECT tag_slug
        FROM tag_exclusions
    """
    rows = await db.fetch_all(query)
    return {row["tag_slug"] for row in rows}


async def add_tag_exclusion(tag_slug: str, created_by: int | None = None) -> dict[str, Any] | None:
    """Add a new tag exclusion."""
    query = """
        INSERT INTO tag_exclusions (tag_slug, created_by, created_at)
        VALUES (%s, %s, %s)
    """
    created_at = datetime.now(timezone.utc)
    try:
        exclusion_id = await db.execute_returning_lastrowid(query, (tag_slug, created_by, created_at))
        return {
            "id": exclusion_id,
            "tag_slug": tag_slug,
            "created_at": created_at,
            "created_by": created_by,
        }
    except Exception:
        return None


async def delete_tag_exclusion(tag_slug: str) -> bool:
    """Delete a tag exclusion by slug."""
    query = """
        DELETE FROM tag_exclusions
        WHERE tag_slug = %s
    """
    # For delete operations, we need to check if any rows were deleted
    # We'll do this by checking if the row exists first, then deleting it
    check_query = "SELECT COUNT(*) as count FROM tag_exclusions WHERE tag_slug = %s"
    result = await db.fetch_one(check_query, (tag_slug,))
    if result and result.get("count", 0) > 0:
        await db.execute(query, (tag_slug,))
        return True
    return False


async def is_tag_excluded(tag_slug: str) -> bool:
    """Check if a tag slug is in the exclusion list."""
    query = """
        SELECT COUNT(*) as count
        FROM tag_exclusions
        WHERE tag_slug = %s
    """
    result = await db.fetch_one(query, (tag_slug,))
    return result["count"] > 0 if result else False
