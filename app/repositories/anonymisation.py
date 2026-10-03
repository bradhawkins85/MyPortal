"""Repository for account anonymisation requests and the anonymisation run.

The request lifecycle mirrors GDPR "right to be forgotten" handling:

* :func:`create_request` records a user's request (status ``pending``).
* A super admin (or an automated run) transitions the request through
  ``executing`` to ``completed`` (or ``failed``), or ``cancelled``.

:func:`anonymise_user` performs the destructive work in a single, idempotent
pass. It is safe against missing optional feature-pack tables (each optional
step is guarded) and re-runnable after a partial failure: running it twice
against the same user produces the same end state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from loguru import logger

from app.core.database import db


# ---------------------------------------------------------------------------
# Placeholder helpers
# ---------------------------------------------------------------------------

_ANON_EMAIL_DOMAIN = "invalid"
_ANON_EMAIL_PREFIX = "anonymised+"
_ANON_FIRST_NAME = "Anonymised"
_ANON_LAST_NAME = "User"


def placeholder_email_for(user_id: int) -> str:
    """Return the deterministic anonymised address for a user.

    Using the numeric user id keeps the address stable across runs, which is
    what makes the whole anonymisation idempotent (the same run always writes
    the same placeholder). The ``invalid`` domain guarantees it is never a
    deliverable mailbox.
    """
    return f"{_ANON_EMAIL_PREFIX}{user_id}@{_ANON_EMAIL_DOMAIN}"


def is_anonymised_email(email: str | None) -> bool:
    """Return True when the address is one of our anonymisation placeholders."""
    if not email:
        return False
    lowered = email.strip().lower()
    return lowered.startswith(_ANON_EMAIL_PREFIX) and lowered.endswith(f"@{_ANON_EMAIL_DOMAIN}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


async def create_request(
    *,
    user_id: int,
    reason: str | None = None,
    requested_by_user_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> int:
    """Persist a new ``pending`` anonymisation request and return its id."""
    reason_clean = (str(reason).strip() or None) if reason else None
    ua_clean = (str(user_agent).strip()[:512] or None) if user_agent else None
    row = await db.execute_returning_lastrowid(
        """
        INSERT INTO account_anonymisation_requests (
            user_id, reason, requested_by_user_id, status, requested_at,
            ip_address, user_agent
        ) VALUES (%s, %s, %s, 'pending', %s, %s, %s)
        """,
        (user_id, reason_clean, requested_by_user_id, _now(), ip_address, ua_clean),
    )
    return int(row)


async def get_request(request_id: int) -> Mapping[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT r.*,
               u.email AS request_user_email,
               e.email AS executed_by_email
        FROM account_anonymisation_requests AS r
        LEFT JOIN users AS u ON u.id = r.user_id
        LEFT JOIN users AS e ON e.id = r.executed_by_user_id
        WHERE r.id = %s
        """,
        (request_id,),
    )
    return dict(row) if row else None


async def get_request_for_user(user_id: int) -> Mapping[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM account_anonymisation_requests WHERE user_id = %s",
        (user_id,),
    )
    return dict(row) if row else None


async def list_requests(
    *,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = (
        """
        SELECT r.*, u.email AS request_user_email, e.email AS executed_by_email
        FROM account_anonymisation_requests AS r
        LEFT JOIN users AS u ON u.id = r.user_id
        LEFT JOIN users AS e ON e.id = r.executed_by_user_id
        """
    )
    params: list[Any] = []
    if status:
        sql += " WHERE r.status = %s"
        params.append(status)
    sql += " ORDER BY r.requested_at DESC, r.id DESC LIMIT %s"
    params.append(max(1, min(int(limit), 1000)))
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows]


async def set_support_ticket(request_id: int, ticket_id: int | None) -> None:
    await db.execute(
        "UPDATE account_anonymisation_requests SET support_ticket_id = %s WHERE id = %s",
        (ticket_id, request_id),
    )


async def _transition(
    request_id: int,
    *,
    from_status: str,
    to_status: str,
    fields: Mapping[str, Any] | None = None,
) -> bool:
    """Atomically move a request ``from_status`` -> ``to_status``.

    Returns True when a row was updated. The ``WHERE status = from_status``
    guard keeps concurrent executions from double-firing.
    """
    columns: dict[str, Any] = {"status": to_status}
    if to_status in ("completed", "failed"):
        columns["executed_at"] = _now()
    columns.update(fields or {})
    assignments = ", ".join(f"{k} = %s" for k in columns)
    params: list[Any] = [columns[k] for k in columns] + [request_id, from_status]
    count = await db.execute_rowcount(
        f"UPDATE account_anonymisation_requests SET {assignments} WHERE id = %s AND status = %s",  # nosec B608
        tuple(params),
    )
    return count > 0


async def mark_executing(request_id: int, *, executed_by_user_id: int) -> bool:
    return await _transition(
        request_id,
        from_status="pending",
        to_status="executing",
        fields={"executed_by_user_id": executed_by_user_id, "error_message": None},
    )


async def mark_completed(request_id: int, *, original_email_hash: str | None = None) -> bool:
    return await _transition(
        request_id,
        from_status="executing",
        to_status="completed",
        fields={"original_email_hash": original_email_hash, "error_message": None},
    )


async def mark_failed(request_id: int, *, error_message: str | None = None) -> bool:
    return await _transition(
        request_id,
        from_status="executing",
        to_status="failed",
        fields={"error_message": error_message},
    )


async def cancel_request(request_id: int) -> bool:
    """Cancel a ``pending`` request (deletes the row so the user can re-request)."""
    count = await db.execute_rowcount(
        "DELETE FROM account_anonymisation_requests WHERE id = %s AND status = 'pending'",
        (request_id,),
    )
    return count > 0


# ---------------------------------------------------------------------------
# The anonymisation run
# ---------------------------------------------------------------------------


async def _safe_update(sql: str, params: Sequence[Any] | None, label: str) -> None:
    """Run an optional anonymisation step, logging (not raising) on failure."""
    try:
        await db.execute(sql, tuple(params) if params is not None else None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Anonymisation step skipped ({}) : {}", label, str(exc))


async def _safe_update_rowcount(sql: str, params: Sequence[Any] | None, label: str) -> int:
    """Like :func:`_safe_update` but returns the affected-row count."""
    try:
        return int(await db.execute_rowcount(sql, tuple(params) if params is not None else None))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Anonymisation step skipped ({}) : {}", label, str(exc))
        return 0


async def _safe_fetch_all(
    sql: str, params: Sequence[Any] | None, label: str
) -> list[dict[str, Any]]:
    try:
        rows = await db.fetch_all(sql, tuple(params) if params is not None else None)
        return [dict(row) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Anonymisation read skipped ({}) : {}", label, str(exc))
        return []


def _in_clause(ids: Sequence[int]) -> tuple[str, list[int]]:
    return ", ".join(["%s"] * len(ids)), list(ids)



async def list_request_ticket_ids(user_id: int) -> list[int]:
    """Return the ids of tickets this user requested (for RAG cleanup)."""
    rows = await _safe_fetch_all(
        "SELECT id FROM tickets WHERE requester_id = %s", (user_id,), "tickets.requester_id"
    )
    return [int(row["id"]) for row in rows if row.get("id") is not None]



async def anonymise_user(
    user_id: int,
    *,
    original_email: str | None = None,
    original_phone: str | None = None,
    placeholder_email: str | None = None,
) -> dict[str, int]:
    """Anonymise every trace of ``user_id``'s PII.

    Returns a per-table row-count summary (for audit logging; contains no
    PII). The ``users`` row update is the single step allowed to raise;
    every other step is guarded so a missing optional table never aborts the
    run.
    """
    email = (original_email or "").strip() or None
    phone = (original_phone or "").strip() or None
    placeholder = placeholder_email or placeholder_email_for(user_id)
    summary: dict[str, int] = {}

    # 1. Identify the staff identity by the original email. The ``staff``
    #    table has no direct link to the portal user row, so the email match
    #    is the only reliable join. RAG deletion is performed by the caller
    #    (it needs the vector-index helper); the ticket ids are exposed via
    #    :func:`list_request_ticket_ids`.
    staff_ids: list[int] = []
    if email:
        staff_ids = [
            row["id"]
            for row in await _safe_fetch_all(
                "SELECT id FROM staff WHERE email = %s", (email,), "staff.email"
            )
        ]

    # 3. Authentication material.
    summary["user_sessions"] = await _safe_update_rowcount(
        "DELETE FROM user_sessions WHERE user_id = %s", (user_id,), "user_sessions.delete"
    )
    summary["user_totp_authenticators"] = await _safe_update_rowcount(
        "DELETE FROM user_totp_authenticators WHERE user_id = %s",
        (user_id,),
        "user_totp_authenticators.delete",
    )
    summary["user_passkeys"] = await _safe_update_rowcount(
        "DELETE FROM user_passkeys WHERE user_id = %s", (user_id,), "user_passkeys.delete"
    )
    summary["passkey_challenges"] = await _safe_update_rowcount(
        "DELETE FROM passkey_challenges WHERE user_id = %s", (user_id,), "passkey_challenges.delete"
    )

    # 4. Staff identity: anonymise (keep the row so foreign references resolve).
    if staff_ids:
        clause, ids = _in_clause(staff_ids)
        summary["staff"] = await _safe_update_rowcount(
            f"UPDATE staff SET first_name = %s, last_name = %s, email = %s, "  # nosec B608
            f"mobile_phone = NULL WHERE id IN ({clause})",
            [_ANON_FIRST_NAME, _ANON_LAST_NAME, placeholder, *ids],
            "staff.anonymise",
        )

    # 5. Tickets: drop the requester links (user pointer and any anonymised-staff).
    if staff_ids:
        clause, ids = _in_clause(staff_ids)
        await _safe_update(
            f"UPDATE tickets SET requester_staff_id = NULL WHERE requester_staff_id IN ({clause})",  # nosec B608
            ids,
            "tickets.null_requester_staff",
        )
    await _safe_update(
        "UPDATE tickets SET requester_id = NULL WHERE requester_id = %s",
        (user_id,),
        "tickets.null_requester",
    )

    # 6. Ticket replies: clear authorship and redact email/phone in the user's
    #    own reply bodies (bounded to author_id, cross-DB REPLACE()).
    if email:
        await _safe_update(
            "UPDATE ticket_replies SET author_email = NULL, author_display_name = NULL "
            "WHERE author_email = %s",
            (email,),
            "ticket_replies.null_email_author",
        )
    await _safe_update(
        "UPDATE ticket_replies SET author_id = NULL, author_display_name = NULL "
        "WHERE author_id = %s",
        (user_id,),
        "ticket_replies.null_author",
    )
    if email:
        await _safe_update(
            "UPDATE ticket_replies SET body = REPLACE(body, %s, 'REDACTED') "
            "WHERE author_id = %s AND body IS NOT NULL",
            (email, user_id),
            "ticket_replies.redact_email",
        )
    if phone:
        await _safe_update(
            "UPDATE ticket_replies SET body = REPLACE(body, %s, 'REDACTED') "
            "WHERE author_id = %s AND body IS NOT NULL",
            (phone, user_id),
            "ticket_replies.redact_phone",
        )

    # 7. Chat messages: redact the body while sender_user_id is still set,
    #    then clear the sender identity (optional feature-pack table).
    if email:
        await _safe_update(
            "UPDATE chat_messages SET body = REPLACE(body, %s, 'REDACTED') "
            "WHERE sender_user_id = %s AND body IS NOT NULL",
            (email, user_id),
            "chat_messages.redact_email",
        )
    if phone:
        await _safe_update(
            "UPDATE chat_messages SET body = REPLACE(body, %s, 'REDACTED') "
            "WHERE sender_user_id = %s AND body IS NOT NULL",
            (phone, user_id),
            "chat_messages.redact_phone",
        )
    summary["chat_messages"] = await _safe_update_rowcount(
        "UPDATE chat_messages SET sender_user_id = NULL, sender_matrix_id = NULL, "
        "sender_display_name = NULL WHERE sender_user_id = %s",
        (user_id,),
        "chat_messages.null_sender",
    )
    # The Matrix user link stores the user's email and an access token, so it
    # is deleted outright rather than partially redacted.
    summary["chat_user_links"] = await _safe_update_rowcount(
        "DELETE FROM chat_user_links WHERE user_id = %s",
        (user_id,),
        "chat_user_links.delete",
    )

    # 8. SMS ticket links matched by phone (optional). The from-number columns
    #    are NOT NULL with a uniqueness constraint, so the rows are removed.
    if phone:
        summary["sms_ticket_links"] = await _safe_update_rowcount(
            "DELETE FROM sms_ticket_links "
            "WHERE from_number = %s OR from_number_normalized = %s",
            (phone, phone),
            "sms_ticket_links.delete",
        )

    # 9. Call recordings: clear the phone numbers and the transcription text
    #    (all stored on the call_recordings row itself; optional).
    if phone:
        summary["call_recordings"] = await _safe_update_rowcount(
            "UPDATE call_recordings SET caller_number = NULL, callee_number = NULL, "
            "transcription = NULL WHERE caller_number = %s OR callee_number = %s",
            (phone, phone),
            "call_recordings.null",
        )

    # 10. Email tracking events for replies this user authored (optional).
    #     The events table only stores the tracking id, so we join through
    #     ticket_replies.author_id.
    if email:
        summary["email_tracking_events"] = await _safe_update_rowcount(
            "DELETE FROM email_tracking_events WHERE tracking_id IN ("
            "SELECT email_tracking_id FROM ticket_replies "
            "WHERE author_id = %s AND email_tracking_id IS NOT NULL)",
            (user_id,),
            "email_tracking_events.delete",
        )

    # 11. The account itself: overwrite identity, clear PII, deactivate.
    #     This is the one step allowed to raise (a genuine failure).
    await db.execute(
        """
        UPDATE users
        SET first_name = %s,
            last_name = %s,
            email = %s,
            password_hash = NULL,
            totp_secret = NULL,
            passkey_user_handle = NULL,
            mobile_phone = NULL,
            email_signature = NULL,
            matrix_user_id = NULL,
            is_active = 0
        WHERE id = %s
        """,
        (_ANON_FIRST_NAME, _ANON_LAST_NAME, placeholder, user_id),
    )
    summary["users"] = 1

    return summary
