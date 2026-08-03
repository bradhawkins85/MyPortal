"""Data access for content-hash based ticket attachment blocking."""
from __future__ import annotations

from typing import Any

from app.core.database import db


async def is_blocked(sha256_hash: str) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM ticket_attachment_blocklist WHERE sha256_hash = ? LIMIT 1",
        (sha256_hash,),
    )
    return row is not None


async def add(
    sha256_hash: str,
    *,
    original_filename: str | None,
    file_size: int,
    mime_type: str | None,
    created_by_user_id: int | None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO ticket_attachment_blocklist
          (sha256_hash, original_filename, file_size, mime_type, created_by_user_id)
        VALUES (?, ?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE
          original_filename = COALESCE(original_filename, VALUES(original_filename)),
          file_size = COALESCE(file_size, VALUES(file_size)),
          mime_type = COALESCE(mime_type, VALUES(mime_type))
        """,
        (sha256_hash, original_filename, file_size, mime_type, created_by_user_id),
    )
    row = await db.fetch_one(
        "SELECT * FROM ticket_attachment_blocklist WHERE sha256_hash = ?",
        (sha256_hash,),
    )
    return dict(row)


async def list_entries() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM ticket_attachment_blocklist ORDER BY created_at DESC, id DESC"
    )
    return [dict(row) for row in rows]


async def delete(entry_id: int) -> bool:
    return (
        await db.execute_rowcount(
            "DELETE FROM ticket_attachment_blocklist WHERE id = ?", (entry_id,)
        )
    ) > 0
