from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_documents(port_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, port_id, file_name, storage_path, content_type, file_size, description,
               uploaded_by, uploaded_at
        FROM port_documents
        WHERE port_id = %s
        ORDER BY uploaded_at DESC, id DESC
        """,
        (port_id,),
    )
    return list(rows)


async def get_document(document_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        """
        SELECT id, port_id, file_name, storage_path, content_type, file_size, description,
               uploaded_by, uploaded_at
        FROM port_documents
        WHERE id = %s
        """,
        (document_id,),
    )


async def create_document(**values: Any) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO port_documents (port_id, file_name, storage_path, content_type, file_size, description, uploaded_by)
        VALUES (%(port_id)s, %(file_name)s, %(storage_path)s, %(content_type)s, %(file_size)s, %(description)s, %(uploaded_by)s)
        """,
        values,
    )
    row = await db.fetch_one(
        """
        SELECT id, port_id, file_name, storage_path, content_type, file_size, description,
               uploaded_by, uploaded_at
        FROM port_documents
        WHERE port_id = %(port_id)s AND storage_path = %(storage_path)s
        ORDER BY uploaded_at DESC, id DESC
        LIMIT 1
        """,
        values,
    )
    if not row:
        raise RuntimeError("Failed to persist port document")
    return row


async def delete_document(document_id: int) -> None:
    await db.execute("DELETE FROM port_documents WHERE id = %s", (document_id,))
