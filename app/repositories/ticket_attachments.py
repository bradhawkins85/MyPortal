"""Repository for ticket attachments data access."""
from __future__ import annotations

from typing import Any

from app.core.database import db
from app.core.logging import log_debug, log_error


async def create_attachment(
    ticket_id: int,
    filename: str,
    original_filename: str,
    file_size: int,
    mime_type: str | None,
    access_level: str,
    uploaded_by_user_id: int | None,
) -> dict[str, Any]:
    """Create a new ticket attachment record."""
    query = """
        INSERT INTO ticket_attachments 
        (ticket_id, filename, original_filename, file_size, mime_type, access_level, uploaded_by_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    params = (ticket_id, filename, original_filename, file_size, mime_type, access_level, uploaded_by_user_id)
    attachment_id = await db.execute(query, params)
    
    log_debug(f"Created attachment {attachment_id} for ticket {ticket_id}")
    
    return await get_attachment(attachment_id)


async def get_attachment(attachment_id: int) -> dict[str, Any] | None:
    """Get an attachment by ID."""
    query = """
        SELECT id, ticket_id, filename, original_filename, file_size, 
               mime_type, access_level, uploaded_by_user_id, uploaded_at
        FROM ticket_attachments
        WHERE id = ?
    """
    row = await db.fetch_one(query, (attachment_id,))
    return dict(row) if row else None


async def get_attachment_by_filename(ticket_id: int, filename: str) -> dict[str, Any] | None:
    """Get an attachment by ticket ID and filename."""
    query = """
        SELECT id, ticket_id, filename, original_filename, file_size, 
               mime_type, access_level, uploaded_by_user_id, uploaded_at
        FROM ticket_attachments
        WHERE ticket_id = ? AND filename = ?
    """
    row = await db.fetch_one(query, (ticket_id, filename))
    return dict(row) if row else None


async def list_attachments(ticket_id: int) -> list[dict[str, Any]]:
    """List all attachments for a ticket."""
    query = """
        SELECT id, ticket_id, filename, original_filename, file_size, 
               mime_type, access_level, uploaded_by_user_id, uploaded_at
        FROM ticket_attachments
        WHERE ticket_id = ?
        ORDER BY uploaded_at ASC
    """
    rows = await db.fetch_all(query, (ticket_id,))
    return [dict(row) for row in rows]


async def update_attachment(attachment_id: int, **fields) -> None:
    """Update an attachment's fields."""
    if not fields:
        return
    
    set_clauses = [f"{key} = ?" for key in fields.keys()]
    values = list(fields.values())
    values.append(attachment_id)
    
    query = f"""
        UPDATE ticket_attachments
        SET {', '.join(set_clauses)}
        WHERE id = ?
    """
    
    await db.execute(query, tuple(values))
    log_debug(f"Updated attachment {attachment_id}")


async def delete_attachment(attachment_id: int) -> None:
    """Delete an attachment."""
    query = "DELETE FROM ticket_attachments WHERE id = ?"
    await db.execute(query, (attachment_id,))
    log_debug(f"Deleted attachment {attachment_id}")


async def count_attachments(ticket_id: int) -> int:
    """Count attachments for a ticket."""
    query = "SELECT COUNT(*) as count FROM ticket_attachments WHERE ticket_id = ?"
    row = await db.fetch_one(query, (ticket_id,))
    return row["count"] if row else 0
