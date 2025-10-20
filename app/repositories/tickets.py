from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db

TicketRecord = dict[str, Any]


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_ticket(row: dict[str, Any]) -> TicketRecord:
    record = dict(row)
    for key in ("id", "company_id", "requester_id", "assigned_user_id"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at", "closed_at"):
        record[key] = _make_aware(record.get(key))
    return record


def _normalise_reply(row: dict[str, Any]) -> TicketRecord:
    record = dict(row)
    for key in ("id", "ticket_id", "author_id"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    record["created_at"] = _make_aware(record.get("created_at"))
    return record


def _normalise_watcher(row: dict[str, Any]) -> TicketRecord:
    record = dict(row)
    for key in ("id", "ticket_id", "user_id"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    record["created_at"] = _make_aware(record.get("created_at"))
    return record


async def create_ticket(
    *,
    subject: str,
    description: str | None,
    requester_id: int | None,
    company_id: int | None,
    assigned_user_id: int | None,
    priority: str,
    status: str,
    category: str | None,
    module_slug: str | None,
    external_reference: str | None,
) -> TicketRecord:
    ticket_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO tickets
            (company_id, requester_id, assigned_user_id, subject, description, status, priority, category, module_slug, external_reference)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            requester_id,
            assigned_user_id,
            subject,
            description,
            status,
            priority,
            category,
            module_slug,
            external_reference,
        ),
    )
    if ticket_id:
        row = await db.fetch_one("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        if row:
            return _normalise_ticket(row)
    fallback_row: dict[str, Any] = {
        "id": ticket_id,
        "company_id": company_id,
        "requester_id": requester_id,
        "assigned_user_id": assigned_user_id,
        "subject": subject,
        "description": description,
        "status": status,
        "priority": priority,
        "category": category,
        "module_slug": module_slug,
        "external_reference": external_reference,
        "created_at": None,
        "updated_at": None,
        "closed_at": None,
    }
    return _normalise_ticket(fallback_row)


async def list_tickets(
    *,
    status: str | None = None,
    module_slug: str | None = None,
    company_id: int | None = None,
    assigned_user_id: int | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TicketRecord]:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    if module_slug:
        where.append("module_slug = %s")
        params.append(module_slug)
    if company_id is not None:
        where.append("company_id = %s")
        params.append(company_id)
    if assigned_user_id is not None:
        where.append("assigned_user_id = %s")
        params.append(assigned_user_id)
    if search:
        wildcard = f"%{search.strip()}%"
        where.append(
            "(subject LIKE %s OR description LIKE %s OR external_reference LIKE %s)"
        )
        params.extend([wildcard, wildcard, wildcard])
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    params.extend([limit, offset])
    rows = await db.fetch_all(
        f"""
        SELECT *
        FROM tickets
        {where_clause}
        ORDER BY updated_at DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [_normalise_ticket(row) for row in rows]


async def count_tickets(
    *,
    status: str | None = None,
    module_slug: str | None = None,
    company_id: int | None = None,
    assigned_user_id: int | None = None,
    search: str | None = None,
) -> int:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    if module_slug:
        where.append("module_slug = %s")
        params.append(module_slug)
    if company_id is not None:
        where.append("company_id = %s")
        params.append(company_id)
    if assigned_user_id is not None:
        where.append("assigned_user_id = %s")
        params.append(assigned_user_id)
    if search:
        wildcard = f"%{search.strip()}%"
        where.append(
            "(subject LIKE %s OR description LIKE %s OR external_reference LIKE %s)"
        )
        params.extend([wildcard, wildcard, wildcard])
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS count FROM tickets{where_clause}",
        tuple(params) if params else None,
    )
    return int(row["count"]) if row else 0


async def get_ticket(ticket_id: int) -> TicketRecord | None:
    row = await db.fetch_one("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    return _normalise_ticket(row) if row else None


async def update_ticket(ticket_id: int, **fields: Any) -> TicketRecord | None:
    if not fields:
        return await get_ticket(ticket_id)
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        assignments.append(f"{key} = %s")
        params.append(value)
    assignments.append("updated_at = UTC_TIMESTAMP(6)")
    query = f"UPDATE tickets SET {', '.join(assignments)} WHERE id = %s"
    params.append(ticket_id)
    await db.execute(query, tuple(params))
    return await get_ticket(ticket_id)


async def set_ticket_status(
    ticket_id: int,
    status: str,
    *,
    closed_at: datetime | None = None,
) -> TicketRecord | None:
    fields: dict[str, Any] = {"status": status}
    if closed_at:
        fields["closed_at"] = closed_at
    elif status not in {"resolved", "closed"}:
        fields["closed_at"] = None
    return await update_ticket(ticket_id, **fields)


async def delete_ticket(ticket_id: int) -> None:
    await db.execute("DELETE FROM tickets WHERE id = %s", (ticket_id,))


async def create_reply(
    *,
    ticket_id: int,
    author_id: int | None,
    body: str,
    is_internal: bool = False,
) -> TicketRecord:
    reply_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_replies (ticket_id, author_id, body, is_internal)
        VALUES (%s, %s, %s, %s)
        """,
        (ticket_id, author_id, body, 1 if is_internal else 0),
    )
    if reply_id:
        row = await db.fetch_one("SELECT * FROM ticket_replies WHERE id = %s", (reply_id,))
        if row:
            return _normalise_reply(row)
    fallback_row: dict[str, Any] = {
        "id": reply_id,
        "ticket_id": ticket_id,
        "author_id": author_id,
        "body": body,
        "is_internal": 1 if is_internal else 0,
        "created_at": None,
    }
    return _normalise_reply(fallback_row)


async def list_replies(ticket_id: int) -> list[TicketRecord]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM ticket_replies
        WHERE ticket_id = %s
        ORDER BY created_at ASC
        """,
        (ticket_id,),
    )
    return [_normalise_reply(row) for row in rows]


async def add_watcher(ticket_id: int, user_id: int) -> None:
    await db.execute(
        """
        INSERT INTO ticket_watchers (ticket_id, user_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)
        """,
        (ticket_id, user_id),
    )


async def remove_watcher(ticket_id: int, user_id: int) -> None:
    await db.execute(
        "DELETE FROM ticket_watchers WHERE ticket_id = %s AND user_id = %s",
        (ticket_id, user_id),
    )


async def list_watchers(ticket_id: int) -> list[TicketRecord]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM ticket_watchers
        WHERE ticket_id = %s
        ORDER BY created_at ASC
        """,
        (ticket_id,),
    )
    return [_normalise_watcher(row) for row in rows]


async def bulk_add_watchers(ticket_id: int, user_ids: Iterable[int]) -> None:
    values: list[tuple[int, int]] = []
    for user_id in user_ids:
        values.append((ticket_id, int(user_id)))
    if not values:
        return
    placeholders = ",".join(["(%s, %s)"] * len(values))
    flat_params: list[Any] = []
    for pair in values:
        flat_params.extend(pair)
    await db.execute(
        f"""
        INSERT INTO ticket_watchers (ticket_id, user_id)
        VALUES {placeholders}
        ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)
        """,
        tuple(flat_params),
    )


async def replace_watchers(ticket_id: int, user_ids: Iterable[int]) -> None:
    await db.execute("DELETE FROM ticket_watchers WHERE ticket_id = %s", (ticket_id,))
    await bulk_add_watchers(ticket_id, user_ids)
