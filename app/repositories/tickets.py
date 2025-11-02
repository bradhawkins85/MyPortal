from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.core.database import db

TicketRecord = dict[str, Any]

_UNSET = object()


def _deserialise_tags(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parts = [segment.strip() for segment in re.split(r"[,\n;]+", text) if segment.strip()]
            return parts
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        if isinstance(parsed, str):
            single = parsed.strip()
            return [single] if single else []
        if isinstance(parsed, dict):
            candidate = parsed.get("tags") or parsed.get("keywords")
            if candidate:
                return _deserialise_tags(candidate)
    return []


def _serialise_tags(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return json.dumps([cleaned], ensure_ascii=False)
    iterable: Iterable[Any]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        iterable = value
    else:
        return None
    tags: list[str] = []
    for item in iterable:
        text = str(item).strip()
        if not text:
            continue
        if text not in tags:
            tags.append(text)
    return json.dumps(tags, ensure_ascii=False) if tags else None


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
    for key in ("created_at", "updated_at", "closed_at", "ai_summary_updated_at"):
        record[key] = _make_aware(record.get(key))
    record["ai_tags"] = _deserialise_tags(record.get("ai_tags"))
    record["ai_tags_updated_at"] = _make_aware(record.get("ai_tags_updated_at"))
    return record


def _normalise_reply(row: dict[str, Any]) -> TicketRecord:
    record = dict(row)
    for key in ("id", "ticket_id", "author_id", "minutes_spent"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    record["created_at"] = _make_aware(record.get("created_at"))
    if "is_billable" in record:
        record["is_billable"] = bool(record.get("is_billable"))
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
    ticket_number: str | None = None,
) -> TicketRecord:
    ticket_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO tickets
            (company_id, requester_id, assigned_user_id, subject, description, status, priority, category, module_slug, external_reference, ticket_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ticket_number,
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
        "ticket_number": ticket_number,
        "ai_summary": None,
        "ai_summary_status": None,
        "ai_summary_model": None,
        "ai_resolution_state": None,
        "ai_summary_updated_at": None,
        "ai_tags": [],
        "ai_tags_status": None,
        "ai_tags_model": None,
        "ai_tags_updated_at": None,
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
    requester_id: int | None = None,
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
    if requester_id is not None:
        where.append("requester_id = %s")
        params.append(requester_id)
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


def _prepare_status_filters(status: str | Sequence[str] | None) -> list[str]:
    if status in (None, ""):
        return []
    if isinstance(status, str):
        candidates = [status]
    else:
        candidates = list(status)
    slugs: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate or "").strip().lower()
        if not text or text in seen:
            continue
        if any(not (char.isalnum() or char in {"_", "-"}) for char in text):
            continue
        seen.add(text)
        slugs.append(text)
    return slugs


async def list_tickets_for_user(
    user_id: int,
    *,
    company_ids: Sequence[int] | None = None,
    search: str | None = None,
    status: str | Sequence[str] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[TicketRecord]:
    """Return recent tickets requested by or watched by the specified user."""

    if user_id <= 0:
        return []

    company_filters = [int(cid) for cid in (company_ids or []) if int(cid) > 0]

    query_parts = [
        "SELECT DISTINCT t.* FROM tickets AS t",
        "LEFT JOIN ticket_watchers AS tw ON tw.ticket_id = t.id AND tw.user_id = %s",
    ]
    params: list[Any] = [user_id]

    conditions = ["(t.requester_id = %s OR tw.user_id = %s)"]
    params.extend([user_id, user_id])

    status_filters = _prepare_status_filters(status)
    if status_filters:
        if len(status_filters) == 1:
            conditions.append("t.status = %s")
            params.append(status_filters[0])
        else:
            placeholders = ", ".join(["%s"] * len(status_filters))
            conditions.append(f"t.status IN ({placeholders})")
            params.extend(status_filters)

    if company_filters:
        placeholders = ", ".join(["%s"] * len(company_filters))
        conditions.append(f"t.company_id IN ({placeholders})")
        params.extend(company_filters)

    search_term = (search or "").strip().lower()
    if search_term:
        like = f"%{search_term}%"
        conditions.append(
            "(LOWER(t.subject) LIKE %s OR LOWER(COALESCE(t.description, '')) LIKE %s)"
        )
        params.extend([like, like])

    query_parts.append("WHERE " + " AND ".join(conditions))
    query_parts.append("ORDER BY t.updated_at DESC")
    query_parts.append("LIMIT %s OFFSET %s")
    params.extend([int(max(1, limit)), int(max(0, offset))])

    rows = await db.fetch_all(" ".join(query_parts), tuple(params))
    return [_normalise_ticket(row) for row in rows]


async def count_tickets_for_user(
    user_id: int,
    *,
    company_ids: Sequence[int] | None = None,
    search: str | None = None,
    status: str | Sequence[str] | None = None,
) -> int:
    """Return the number of tickets requested by or watched by the specified user."""

    if user_id <= 0:
        return 0

    company_filters = [int(cid) for cid in (company_ids or []) if int(cid) > 0]

    query_parts = [
        "SELECT COUNT(DISTINCT t.id) AS count FROM tickets AS t",
        "LEFT JOIN ticket_watchers AS tw ON tw.ticket_id = t.id AND tw.user_id = %s",
    ]
    params: list[Any] = [user_id]

    conditions = ["(t.requester_id = %s OR tw.user_id = %s)"]
    params.extend([user_id, user_id])

    status_filters = _prepare_status_filters(status)
    if status_filters:
        if len(status_filters) == 1:
            conditions.append("t.status = %s")
            params.append(status_filters[0])
        else:
            placeholders = ", ".join(["%s"] * len(status_filters))
            conditions.append(f"t.status IN ({placeholders})")
            params.extend(status_filters)

    if company_filters:
        placeholders = ", ".join(["%s"] * len(company_filters))
        conditions.append(f"t.company_id IN ({placeholders})")
        params.extend(company_filters)

    search_term = (search or "").strip().lower()
    if search_term:
        like = f"%{search_term}%"
        conditions.append(
            "(LOWER(t.subject) LIKE %s OR LOWER(COALESCE(t.description, '')) LIKE %s)"
        )
        params.extend([like, like])

    query_parts.append("WHERE " + " AND ".join(conditions))

    row = await db.fetch_one(" ".join(query_parts), tuple(params))
    return int(row["count"]) if row else 0


async def count_tickets(
    *,
    status: str | None = None,
    module_slug: str | None = None,
    company_id: int | None = None,
    assigned_user_id: int | None = None,
    search: str | None = None,
    requester_id: int | None = None,
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
    if requester_id is not None:
        where.append("requester_id = %s")
        params.append(requester_id)
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


async def is_ticket_watcher(ticket_id: int, user_id: int) -> bool:
    if ticket_id <= 0 or user_id <= 0:
        return False
    row = await db.fetch_one(
        "SELECT 1 FROM ticket_watchers WHERE ticket_id = %s AND user_id = %s LIMIT 1",
        (ticket_id, user_id),
    )
    return bool(row)


async def get_ticket_by_external_reference(external_reference: str) -> TicketRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM tickets WHERE external_reference = %s",
        (external_reference,),
    )
    return _normalise_ticket(row) if row else None


async def update_ticket(ticket_id: int, **fields: Any) -> TicketRecord | None:
    if not fields:
        return await get_ticket(ticket_id)
    assignments: list[str] = []
    params: list[Any] = []
    override_updated_at = None
    if "updated_at" in fields:
        override_updated_at = fields.pop("updated_at")
    for key, value in fields.items():
        if key == "ai_tags":
            assignments.append(f"{key} = %s")
            params.append(_serialise_tags(value))
            continue
        assignments.append(f"{key} = %s")
        params.append(value)
    if override_updated_at is not None:
        assignments.append("updated_at = %s")
        params.append(override_updated_at)
    else:
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


async def delete_tickets(ticket_ids: Iterable[int]) -> int:
    """Delete multiple tickets by their identifiers.

    Returns the number of tickets that were removed. Invalid identifiers are
    ignored so callers can pass raw form values safely.
    """

    normalised_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in ticket_ids:
        try:
            identifier = int(raw_id)
        except (TypeError, ValueError):
            continue
        if identifier <= 0 or identifier in seen:
            continue
        seen.add(identifier)
        normalised_ids.append(identifier)

    if not normalised_ids:
        return 0

    placeholders = ", ".join(["%s"] * len(normalised_ids))
    params = tuple(normalised_ids)

    existing = await db.fetch_one(
        f"SELECT COUNT(*) AS total FROM tickets WHERE id IN ({placeholders})",
        params,
    )
    await db.execute(
        f"DELETE FROM tickets WHERE id IN ({placeholders})",
        params,
    )
    if not existing:
        return 0
    try:
        return int(existing.get("total") or 0)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 0


async def create_reply(
    *,
    ticket_id: int,
    author_id: int | None,
    body: str,
    is_internal: bool = False,
    minutes_spent: int | None = None,
    is_billable: bool = False,
    external_reference: str | None = None,
    created_at: datetime | None = None,
) -> TicketRecord:
    columns = ["ticket_id", "author_id", "body", "is_internal", "is_billable"]
    params: list[Any] = [
        ticket_id,
        author_id,
        body,
        1 if is_internal else 0,
        1 if is_billable else 0,
    ]
    if minutes_spent is not None:
        columns.append("minutes_spent")
        params.append(minutes_spent)
    if external_reference is not None:
        columns.append("external_reference")
        params.append(external_reference)
    if created_at is not None:
        columns.append("created_at")
        params.append(created_at)
    placeholders = ", ".join(["%s"] * len(columns))
    reply_id = await db.execute_returning_lastrowid(
        f"""
        INSERT INTO ticket_replies ({', '.join(columns)})
        VALUES ({placeholders})
        """,
        tuple(params),
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
        "minutes_spent": minutes_spent,
        "is_billable": 1 if is_billable else 0,
        "external_reference": external_reference,
        "created_at": created_at,
    }
    return _normalise_reply(fallback_row)


async def list_replies(ticket_id: int, *, include_internal: bool = True) -> list[TicketRecord]:
    where = "ticket_id = %s"
    params: list[Any] = [ticket_id]
    if not include_internal:
        where += " AND is_internal = 0"
    rows = await db.fetch_all(
        f"""
        SELECT *
        FROM ticket_replies
        WHERE {where}
        ORDER BY created_at ASC
        """,
        tuple(params),
    )
    return [_normalise_reply(row) for row in rows]


async def get_reply_by_id(reply_id: int) -> TicketRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM ticket_replies WHERE id = %s",
        (reply_id,),
    )
    if row:
        return _normalise_reply(row)
    return None


async def update_reply(
    reply_id: int,
    *,
    minutes_spent: int | None | object = _UNSET,
    is_billable: bool | object = _UNSET,
) -> TicketRecord | None:
    updates: list[str] = []
    params: list[Any] = []
    if minutes_spent is not _UNSET:
        if minutes_spent is None:
            updates.append("minutes_spent = NULL")
        else:
            updates.append("minutes_spent = %s")
            params.append(int(minutes_spent))
    if is_billable is not _UNSET:
        updates.append("is_billable = %s")
        params.append(1 if bool(is_billable) else 0)
    if updates:
        params.append(reply_id)
        await db.execute(
            f"""
            UPDATE ticket_replies
            SET {', '.join(updates)}
            WHERE id = %s
            """,
            tuple(params),
        )
    return await get_reply_by_id(reply_id)


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
