from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


async def get_room(room_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM chat_rooms WHERE id = %s",
        (room_id,),
    )
    return dict(row) if row else None


async def get_room_by_matrix_id(matrix_room_id: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM chat_rooms WHERE matrix_room_id = %s",
        (matrix_room_id,),
    )
    return dict(row) if row else None


async def list_rooms(
    *,
    company_id: int | None = None,
    user_id: int | None = None,
    status: str | None = None,
    unattended_only: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []

    if company_id is not None:
        clauses.append("r.company_id = %s")
        params.append(company_id)

    if user_id is not None:
        clauses.append(
            "r.id IN (SELECT room_id FROM chat_room_participants WHERE user_id = %s)"
        )
        params.append(user_id)

    if status:
        clauses.append("r.status = %s")
        params.append(status)

    # Use LEFT JOIN + NULL check instead of NOT IN for better performance on large tables
    unattended_join = ""
    if unattended_only:
        unattended_join = (
            "LEFT JOIN chat_room_participants tp_unattended "
            "ON tp_unattended.room_id = r.id AND tp_unattended.role IN ('technician','admin')"
        )
        clauses.append("tp_unattended.room_id IS NULL")

    where = " AND ".join(clauses)
    params.extend([limit, offset])
    rows = await db.fetch_all(
        f"""SELECT r.*,
               (SELECT COUNT(*) FROM chat_room_participants p WHERE p.room_id = r.id AND p.role IN ('technician','admin')) AS tech_participant_count,
               u.display_name AS assigned_tech_display_name
            FROM chat_rooms r
            LEFT JOIN users u ON u.id = r.assigned_tech_user_id
            {unattended_join}
            WHERE {where}
            ORDER BY r.updated_at DESC LIMIT %s OFFSET %s""",
        tuple(params),
    )
    return [dict(r) for r in rows]


async def create_room(
    *,
    subject: str,
    matrix_room_id: str,
    room_alias: str | None,
    created_by_user_id: int,
    company_id: int,
    linked_ticket_id: int | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    room_id = await db.execute_returning_lastrowid(
        """INSERT INTO chat_rooms
           (matrix_room_id, room_alias, created_by_user_id, company_id, subject,
            status, created_at, updated_at, linked_ticket_id)
           VALUES (%s, %s, %s, %s, %s, 'open', %s, %s, %s)""",
        (matrix_room_id, room_alias, created_by_user_id, company_id, subject,
         now, now, linked_ticket_id),
    )
    row = await db.fetch_one("SELECT * FROM chat_rooms WHERE id = %s", (room_id,))
    return dict(row) if row else {}


_ROOM_UPDATABLE_FIELDS = frozenset({
    "status", "updated_at", "last_message_at", "subject", "linked_ticket_id",
    "assigned_tech_user_id",
})

_INVITE_UPDATABLE_FIELDS = frozenset({
    "status", "provisioned_matrix_user_id", "temporary_password_hash",
    "delivery_method", "expires_at",
})


async def update_room(room_id: int, **fields: Any) -> None:
    if not fields:
        return
    invalid = set(fields) - _ROOM_UPDATABLE_FIELDS
    if invalid:
        raise ValueError(f"Cannot update chat_rooms fields: {invalid}")
    set_clauses = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [room_id]
    await db.execute(
        f"UPDATE chat_rooms SET {set_clauses} WHERE id = %s",
        tuple(params),
    )


async def assign_tech(room_id: int, tech_user_id: int) -> None:
    """Assign a technician to a chat room (only if currently unassigned)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        """UPDATE chat_rooms
           SET assigned_tech_user_id = %s, updated_at = %s
           WHERE id = %s AND assigned_tech_user_id IS NULL""",
        (tech_user_id, now, room_id),
    )


async def reassign_tech(room_id: int, tech_user_id: int | None) -> None:
    """Forcibly set (or clear) the assigned technician on a chat room."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        "UPDATE chat_rooms SET assigned_tech_user_id = %s, updated_at = %s WHERE id = %s",
        (tech_user_id, now, room_id),
    )


async def get_participants(room_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM chat_room_participants WHERE room_id = %s",
        (room_id,),
    )
    return [dict(r) for r in rows]


async def get_participant(
    room_id: int,
    *,
    user_id: int | None = None,
    matrix_user_id: str | None = None,
) -> dict[str, Any] | None:
    if user_id is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_room_participants WHERE room_id = %s AND user_id = %s",
            (room_id, user_id),
        )
    elif matrix_user_id is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_room_participants WHERE room_id = %s AND matrix_user_id = %s",
            (room_id, matrix_user_id),
        )
    else:
        return None
    return dict(row) if row else None


async def add_participant(
    room_id: int,
    matrix_user_id: str,
    role: str,
    *,
    user_id: int | None = None,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if db.is_sqlite():
        await db.execute(
            """INSERT OR IGNORE INTO chat_room_participants
               (room_id, user_id, matrix_user_id, role, joined_at)
               VALUES (?, ?, ?, ?, ?)""",
            (room_id, user_id, matrix_user_id, role, now),
        )
    else:
        await db.execute(
            """INSERT INTO chat_room_participants
               (room_id, user_id, matrix_user_id, role, joined_at)
               VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE role = VALUES(role)""",
            (room_id, user_id, matrix_user_id, role, now),
        )


async def get_messages(
    room_id: int,
    *,
    offset: int = 0,
    limit: int = 50,
    before_event_id: str | None = None,
) -> list[dict[str, Any]]:
    if before_event_id:
        row = await db.fetch_one(
            "SELECT sent_at FROM chat_messages WHERE matrix_event_id = %s",
            (before_event_id,),
        )
        if row:
            rows = await db.fetch_all(
                """SELECT * FROM chat_messages
                   WHERE room_id = %s AND sent_at < %s AND redacted_at IS NULL
                   ORDER BY sent_at DESC LIMIT %s OFFSET %s""",
                (room_id, row["sent_at"], limit, offset),
            )
            return [dict(r) for r in reversed(rows)]

    rows = await db.fetch_all(
        """SELECT * FROM chat_messages
           WHERE room_id = %s AND redacted_at IS NULL
           ORDER BY sent_at ASC LIMIT %s OFFSET %s""",
        (room_id, limit, offset),
    )
    return [dict(r) for r in rows]


async def get_message_by_event_id(matrix_event_id: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM chat_messages WHERE matrix_event_id = %s",
        (matrix_event_id,),
    )
    return dict(row) if row else None


async def add_message(
    *,
    room_id: int,
    matrix_event_id: str | None,
    sender_matrix_id: str,
    body: str,
    msgtype: str = "m.text",
    sender_user_id: int | None = None,
    sent_at: datetime | None = None,
) -> dict[str, Any]:
    if sent_at is None:
        sent_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if db.is_sqlite():
        msg_id = await db.execute_returning_lastrowid(
            """INSERT OR IGNORE INTO chat_messages
               (room_id, matrix_event_id, sender_matrix_id, sender_user_id,
                body, msgtype, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (room_id, matrix_event_id, sender_matrix_id, sender_user_id,
             body, msgtype, sent_at),
        )
        if msg_id:
            row = await db.fetch_one("SELECT * FROM chat_messages WHERE id = ?", (msg_id,))
        else:
            row = await db.fetch_one(
                "SELECT * FROM chat_messages WHERE matrix_event_id = ?",
                (matrix_event_id,),
            )
    else:
        msg_id = await db.execute_returning_lastrowid(
            """INSERT IGNORE INTO chat_messages
               (room_id, matrix_event_id, sender_matrix_id, sender_user_id,
                body, msgtype, sent_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (room_id, matrix_event_id, sender_matrix_id, sender_user_id,
             body, msgtype, sent_at),
        )
        if msg_id:
            row = await db.fetch_one("SELECT * FROM chat_messages WHERE id = %s", (msg_id,))
        else:
            row = await db.fetch_one(
                "SELECT * FROM chat_messages WHERE matrix_event_id = %s",
                (matrix_event_id,),
            )
    return dict(row) if row else {}


async def get_chat_user_link(
    *,
    user_id: int | None = None,
    email: str | None = None,
    matrix_user_id: str | None = None,
) -> dict[str, Any] | None:
    if matrix_user_id is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_user_links WHERE matrix_user_id = %s",
            (matrix_user_id,),
        )
    elif user_id is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_user_links WHERE user_id = %s",
            (user_id,),
        )
    elif email is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_user_links WHERE email = %s",
            (email,),
        )
    else:
        return None
    return dict(row) if row else None


async def upsert_chat_user_link(
    matrix_user_id: str,
    *,
    access_token_encrypted: str | None = None,
    device_id: str | None = None,
    user_id: int | None = None,
    email: str | None = None,
    is_provisioned: bool = False,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if db.is_sqlite():
        await db.execute(
            """INSERT OR REPLACE INTO chat_user_links
               (matrix_user_id, user_id, email, access_token_encrypted,
                device_id, is_provisioned, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (matrix_user_id, user_id, email, access_token_encrypted,
             device_id, 1 if is_provisioned else 0, now, now),
        )
    else:
        await db.execute(
            """INSERT INTO chat_user_links
               (matrix_user_id, user_id, email, access_token_encrypted,
                device_id, is_provisioned, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 user_id = COALESCE(VALUES(user_id), user_id),
                 email = COALESCE(VALUES(email), email),
                 access_token_encrypted = COALESCE(VALUES(access_token_encrypted), access_token_encrypted),
                 device_id = COALESCE(VALUES(device_id), device_id),
                 is_provisioned = VALUES(is_provisioned),
                 updated_at = VALUES(updated_at)""",
            (matrix_user_id, user_id, email, access_token_encrypted,
             device_id, 1 if is_provisioned else 0, now, now),
        )


async def get_invite(
    *,
    invite_token: str | None = None,
    invite_id: int | None = None,
) -> dict[str, Any] | None:
    if invite_token is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_invites WHERE invite_token = %s",
            (invite_token,),
        )
    elif invite_id is not None:
        row = await db.fetch_one(
            "SELECT * FROM chat_invites WHERE id = %s",
            (invite_id,),
        )
    else:
        return None
    return dict(row) if row else None


async def list_invites(room_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM chat_invites WHERE room_id = %s ORDER BY created_at DESC",
        (room_id,),
    )
    return [dict(r) for r in rows]


async def create_invite(
    *,
    room_id: int,
    created_by_user_id: int,
    invite_token: str,
    delivery_method: str,
    target_email: str | None = None,
    target_phone: str | None = None,
    target_display_name: str | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    invite_id = await db.execute_returning_lastrowid(
        """INSERT INTO chat_invites
           (room_id, created_by_user_id, target_email, target_phone,
            target_display_name, invite_token, delivery_method, status,
            expires_at, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)""",
        (room_id, created_by_user_id, target_email, target_phone,
         target_display_name, invite_token, delivery_method, expires_at, now),
    )
    row = await db.fetch_one("SELECT * FROM chat_invites WHERE id = %s", (invite_id,))
    return dict(row) if row else {}


async def update_invite(invite_id: int, **fields: Any) -> None:
    if not fields:
        return
    invalid = set(fields) - _INVITE_UPDATABLE_FIELDS
    if invalid:
        raise ValueError(f"Cannot update chat_invites fields: {invalid}")
    set_clauses = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [invite_id]
    await db.execute(
        f"UPDATE chat_invites SET {set_clauses} WHERE id = %s",
        tuple(params),
    )


async def get_sync_state() -> str | None:
    row = await db.fetch_one(
        "SELECT next_batch FROM matrix_sync_state WHERE id = 1",
    )
    if not row:
        return None
    return row["next_batch"]


async def save_sync_state(next_batch: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if db.is_sqlite():
        await db.execute(
            """INSERT OR REPLACE INTO matrix_sync_state (id, next_batch, updated_at)
               VALUES (1, ?, ?)""",
            (next_batch, now),
        )
    else:
        await db.execute(
            """INSERT INTO matrix_sync_state (id, next_batch, updated_at)
               VALUES (1, %s, %s)
               ON DUPLICATE KEY UPDATE next_batch = VALUES(next_batch), updated_at = VALUES(updated_at)""",
            (next_batch, now),
        )
