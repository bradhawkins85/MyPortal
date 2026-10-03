"""Repository for account anonymisation requests and the anonymisation run.

Request lifecycle (issue #4554):

* :func:`create_request` records a user's request as ``pending`` (or re-opens
  a ``rejected`` one, since there is one row per user).
* A super admin approves it (``approved``) and the run executes, after which
  it is ``completed``; or the admin rejects it (``rejected``) with a reason.
* A run that fails part-way stays ``approved`` with ``error_message`` set and
  can be retried: every step is idempotent.

:func:`anonymise_user` performs the destructive database work. Writes run in a
single transaction on MySQL so the account is never left half-anonymised. Steps
against optional feature-pack tables are guarded so a missing table never
aborts the run.

All SQL uses ``?`` placeholders, which :mod:`app.core.database` adapts for
MySQL, so the same statements run on the SQLite fallback.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence

from loguru import logger

from app.core.database import db


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_COMPLETED = "completed"
OPEN_STATUSES = (STATUS_PENDING, STATUS_APPROVED)

ANON_FIRST_NAME = "Anonymised"
ANON_LAST_NAME = "user"
ANON_DISPLAY_NAME = "Anonymised user"
REDACTED = "[redacted]"
# Not a valid bcrypt or PBKDF2 hash, so password verification always fails.
UNUSABLE_PASSWORD_HASH = "!anonymised"

_ANON_EMAIL_DOMAIN = "invalid"
_ANON_EMAIL_PREFIX = "anonymised+"


# ---------------------------------------------------------------------------
# Placeholder helpers
# ---------------------------------------------------------------------------


def placeholder_email_for(user_id: int) -> str:
    """Return the deterministic anonymised address for a user.

    Using the numeric id keeps the address stable across runs, which keeps
    the run idempotent. The ``invalid`` TLD is reserved (RFC 2606), so the
    address is never deliverable.
    """
    return _ANON_EMAIL_PREFIX + str(int(user_id)) + "@" + _ANON_EMAIL_DOMAIN


def placeholder_staff_email_for(staff_id: int) -> str:
    """Return the anonymised address for a staff record (unique per row)."""
    return _ANON_EMAIL_PREFIX + "staff" + str(int(staff_id)) + "@" + _ANON_EMAIL_DOMAIN


def is_anonymised_email(email: str | None) -> bool:
    """Return True when the address is one of our anonymisation placeholders."""
    if not email:
        return False
    lowered = email.strip().lower()
    return lowered.startswith(_ANON_EMAIL_PREFIX) and lowered.endswith("@" + _ANON_EMAIL_DOMAIN)


def email_hash(email: str | None) -> str | None:
    """Return the one-way hash kept for an anonymised address (or ``None``)."""
    normalised = (email or "").strip().lower()
    if not normalised:
        return None
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------

_REQUEST_SELECT = (
    "SELECT r.*, u.email AS request_user_email, "
    "e.email AS executed_by_email, d.email AS decided_by_email, "
    "q.email AS requested_by_email "
    "FROM account_anonymisation_requests AS r "
    "LEFT JOIN users AS u ON u.id = r.user_id "
    "LEFT JOIN users AS e ON e.id = r.executed_by_user_id "
    "LEFT JOIN users AS d ON d.id = r.decided_by "
    "LEFT JOIN users AS q ON q.id = r.requested_by_user_id"
)


def _clean_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


async def create_request(
    *,
    user_id: int,
    reason: str | None = None,
    requested_by_user_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[int, bool]:
    """Open a ``pending`` request for *user_id*.

    Returns ``(request_id, created)``. ``created`` is False when the user
    already has an open (pending or approved) or completed request; that row
    is returned unchanged so a re-submission never creates a duplicate. A
    rejected request is re-opened, since the table holds one row per user.
    """
    params = (
        _clean_text(reason, 2000),
        requested_by_user_id,
        _now(),
        _clean_text(ip_address, 45),
        _clean_text(user_agent, 512),
    )
    existing = await get_request_for_user(user_id)
    if existing is not None:
        if existing.get("status") != STATUS_REJECTED:
            return int(existing["id"]), False
        reopened = await db.execute_rowcount(
            "UPDATE account_anonymisation_requests SET status = ?, reason = ?, "
            "requested_by_user_id = ?, requested_at = ?, ip_address = ?, user_agent = ?, "
            "decided_by = NULL, decided_at = NULL, notes = NULL, error_message = NULL, "
            "support_ticket_id = NULL WHERE id = ? AND status = ?",
            (STATUS_PENDING, *params, int(existing["id"]), STATUS_REJECTED),
        )
        return int(existing["id"]), reopened > 0
    try:
        request_id = await db.execute_returning_lastrowid(
            "INSERT INTO account_anonymisation_requests "
            "(user_id, status, reason, requested_by_user_id, requested_at, ip_address, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, STATUS_PENDING, *params),
        )
    except Exception:
        # A concurrent submission won the race on the unique user_id key.
        existing = await get_request_for_user(user_id)
        if existing is None:
            raise
        return int(existing["id"]), False
    return int(request_id), True


async def get_request(request_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(_REQUEST_SELECT + " WHERE r.id = ?", (request_id,))
    return dict(row) if row else None


async def get_request_for_user(user_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM account_anonymisation_requests WHERE user_id = ?",
        (user_id,),
    )
    return dict(row) if row else None


async def list_requests(*, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = _REQUEST_SELECT
    params: list[Any] = []
    if status:
        sql += " WHERE r.status = ?"
        params.append(status)
    sql += " ORDER BY r.requested_at DESC, r.id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows]


async def set_support_ticket(request_id: int, ticket_id: int | None) -> None:
    await db.execute(
        "UPDATE account_anonymisation_requests SET support_ticket_id = ? WHERE id = ?",
        (ticket_id, request_id),
    )


async def mark_approved(
    request_id: int,
    *,
    decided_by: int,
    original_email_hash: str | None,
    notes: str | None = None,
) -> bool:
    """Move a ``pending`` request to ``approved``.

    The ``WHERE status`` guard means only one of two concurrent approvals
    wins. The email hash is stored now so it survives a retry after the
    user row has already been overwritten.
    """
    now = _now()
    count = await db.execute_rowcount(
        "UPDATE account_anonymisation_requests SET status = ?, decided_by = ?, decided_at = ?, "
        "executed_by_user_id = ?, original_email_hash = ?, notes = ?, error_message = NULL "
        "WHERE id = ? AND status = ?",
        (
            STATUS_APPROVED,
            decided_by,
            now,
            decided_by,
            original_email_hash,
            _clean_text(notes, 2000),
            request_id,
            STATUS_PENDING,
        ),
    )
    return count > 0


async def mark_rejected(request_id: int, *, decided_by: int, notes: str) -> bool:
    count = await db.execute_rowcount(
        "UPDATE account_anonymisation_requests SET status = ?, decided_by = ?, decided_at = ?, "
        "notes = ? WHERE id = ? AND status = ?",
        (STATUS_REJECTED, decided_by, _now(), _clean_text(notes, 2000), request_id, STATUS_PENDING),
    )
    return count > 0


async def mark_completed(request_id: int) -> bool:
    """Finish an approved request, dropping the requester's IP and user agent."""
    now = _now()
    count = await db.execute_rowcount(
        "UPDATE account_anonymisation_requests SET status = ?, completed_at = ?, executed_at = ?, "
        "error_message = NULL, ip_address = NULL, user_agent = NULL WHERE id = ? AND status = ?",
        (STATUS_COMPLETED, now, now, request_id, STATUS_APPROVED),
    )
    return count > 0


async def record_error(request_id: int, error_message: str | None) -> None:
    """Keep an approved request retryable, noting why the last run failed."""
    await db.execute(
        "UPDATE account_anonymisation_requests SET error_message = ? WHERE id = ? AND status = ?",
        (_clean_text(error_message, 2000), request_id, STATUS_APPROVED),
    )


async def list_anonymised_email_hashes(emails: Iterable[str]) -> set[str]:
    """Return the hashes of *emails* that belong to anonymised accounts."""
    hashes = sorted({h for h in (email_hash(e) for e in emails) if h})
    if not hashes:
        return set()
    rows = await db.fetch_all(
        "SELECT original_email_hash FROM account_anonymisation_requests "  # nosec B608 - only "?" placeholders are joined in
        "WHERE original_email_hash IN (" + _placeholders(hashes) + ")",
        tuple(hashes),
    )
    return {str(row["original_email_hash"]) for row in rows if row.get("original_email_hash")}


# ---------------------------------------------------------------------------
# Data gathered before the run
# ---------------------------------------------------------------------------


async def _safe_fetch_all(sql: str, params: Sequence[Any], label: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in await db.fetch_all(sql, tuple(params))]
    except Exception as exc:  # optional feature-pack table missing
        logger.warning("Anonymisation read skipped ({}): {}", label, type(exc).__name__)
        return []


def _ids(rows: Iterable[Mapping[str, Any]], key: str = "id") -> list[int]:
    return sorted({int(row[key]) for row in rows if row.get(key) is not None})


async def list_staff_for_user(user_id: int, email: str | None) -> list[dict[str, Any]]:
    """Staff records linked to the user by ``portal_user_id`` or email."""
    rows = await _safe_fetch_all(
        "SELECT id, mobile_phone FROM staff WHERE portal_user_id = ?",
        (user_id,),
        "staff.portal_user_id",
    )
    if email:
        rows += await _safe_fetch_all(
            "SELECT id, mobile_phone FROM staff WHERE LOWER(email) = ?",
            (email.strip().lower(),),
            "staff.email",
        )
    unique: dict[int, dict[str, Any]] = {}
    for row in rows:
        unique[int(row["id"])] = row
    return [unique[key] for key in sorted(unique)]


async def list_requested_ticket_ids(user_id: int, staff_ids: Sequence[int] = ()) -> list[int]:
    """Tickets the user (or their staff record) requested."""
    rows = await _safe_fetch_all(
        "SELECT id FROM tickets WHERE requester_id = ?", (user_id,), "tickets.requester_id"
    )
    if staff_ids:
        rows += await _safe_fetch_all(
            "SELECT id FROM tickets WHERE requester_staff_id IN (" + _placeholders(staff_ids) + ")",  # nosec B608 - only "?" placeholders are joined in
            tuple(staff_ids),
            "tickets.requester_staff_id",
        )
    return _ids(rows)


async def list_call_recordings(phones: Sequence[str], staff_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Call recordings for the person's phone numbers or staff records."""
    rows: list[dict[str, Any]] = []
    if phones:
        rows += await _safe_fetch_all(
            "SELECT id, file_path FROM call_recordings WHERE phone_number IN (" + _placeholders(phones) + ")",  # nosec B608 - only "?" placeholders are joined in
            tuple(phones),
            "call_recordings.phone_number",
        )
    if staff_ids:
        marks = _placeholders(staff_ids)
        rows += await _safe_fetch_all(
            "SELECT id, file_path FROM call_recordings WHERE caller_staff_id IN (" + marks + ") "  # nosec B608 - only "?" placeholders are joined in
            "OR callee_staff_id IN (" + marks + ")",
            tuple(staff_ids) + tuple(staff_ids),
            "call_recordings.staff",
        )
    unique = {int(row["id"]): row for row in rows if row.get("id") is not None}
    return [unique[key] for key in sorted(unique)]


async def list_audio_attachments(ticket_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Audio attachments (voicemail) on the person's tickets."""
    if not ticket_ids:
        return []
    rows = await _safe_fetch_all(
        "SELECT id, ticket_id, filename, original_filename, mime_type FROM ticket_attachments "  # nosec B608 - only "?" placeholders are joined in
        "WHERE ticket_id IN (" + _placeholders(ticket_ids) + ")",
        tuple(ticket_ids),
        "ticket_attachments",
    )
    return [row for row in rows if _is_audio(row)]


_AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".wma", ".amr", ".webm")


def _is_audio(attachment: Mapping[str, Any]) -> bool:
    mime = str(attachment.get("mime_type") or "").strip().lower()
    if mime.startswith("audio/"):
        return True
    name = str(attachment.get("original_filename") or attachment.get("filename") or "").lower()
    return name.endswith(_AUDIO_EXTENSIONS)


# ---------------------------------------------------------------------------
# The anonymisation run
# ---------------------------------------------------------------------------


class _Writer:
    """Runs write statements, either on one transaction or autocommitted."""

    def __init__(self, cursor: Any | None = None) -> None:
        self._cursor = cursor

    async def _execute(self, sql: str, params: Sequence[Any]) -> int:
        if self._cursor is None:
            return int(await db.execute_rowcount(sql, tuple(params)))
        adapted_sql, adapted_params = db._adapt_params_for_mysql(sql, tuple(params))
        await self._cursor.execute(adapted_sql, adapted_params)
        return int(self._cursor.rowcount or 0)

    async def required(self, sql: str, params: Sequence[Any]) -> int:
        return await self._execute(sql, params)

    async def optional(self, sql: str, params: Sequence[Any], label: str) -> int:
        """Run a step against a table or column that may not exist on this install.

        Only "no such table/column" errors are skipped. On InnoDB those roll
        back the one statement without aborting the surrounding transaction;
        any other error is raised so the whole run rolls back.
        """
        try:
            return await self._execute(sql, params)
        except Exception as exc:
            if not _is_missing_schema_error(exc):
                raise
            logger.warning("Anonymisation step skipped ({}): {}", label, type(exc).__name__)
            return 0


# MySQL/MariaDB: 1146 table doesn't exist, 1054 unknown column.
_MISSING_SCHEMA_CODES = {1146, 1054}


def _is_missing_schema_error(exc: Exception) -> bool:
    code = exc.args[0] if exc.args else None
    if isinstance(code, int):
        return code in _MISSING_SCHEMA_CODES
    message = str(exc).lower()
    return "no such table" in message or "no such column" in message


# Rows that only describe the person (sign-in material, sessions, tokens,
# personal integrations). Deleted outright.
_PER_USER_DELETES = {
    "user_sessions": "DELETE FROM user_sessions WHERE user_id = ?",
    "user_totp_authenticators": "DELETE FROM user_totp_authenticators WHERE user_id = ?",
    "user_passkeys": "DELETE FROM user_passkeys WHERE user_id = ?",
    "passkey_challenges": "DELETE FROM passkey_challenges WHERE user_id = ?",
    "password_tokens": "DELETE FROM password_tokens WHERE user_id = ?",
    # An unused signup verification link would otherwise re-activate the account.
    "account_verification_tokens": "DELETE FROM account_verification_tokens WHERE user_id = ?",
    "user_m365_contact_integrations": "DELETE FROM user_m365_contact_integrations WHERE user_id = ?",
    "user_click_to_call_settings": "DELETE FROM user_click_to_call_settings WHERE user_id = ?",
    # The Matrix link stores the user's email and an access token.
    "chat_user_links": "DELETE FROM chat_user_links WHERE user_id = ?",
}


@asynccontextmanager
async def _transaction() -> AsyncIterator[_Writer]:
    if db.is_sqlite():
        # The SQLite fallback shares one connection and autocommits; the run is
        # idempotent, so a failed run is simply retried.
        yield _Writer()
        return
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await conn.begin()
            try:
                yield _Writer(cursor)
            except BaseException:
                await conn.rollback()
                raise
            await conn.commit()


async def anonymise_user(
    user_id: int,
    *,
    original_email: str | None,
    phones: Sequence[str] = (),
    staff_ids: Sequence[int] = (),
    ticket_ids: Sequence[int] = (),
    call_recording_ids: Sequence[int] = (),
    attachment_ids: Sequence[int] = (),
) -> dict[str, int]:
    """Strip the personal data of *user_id* from every listed table.

    Business records (tickets, replies, invoices, orders) are kept, still
    linked to the now-anonymised user row, but with names, email addresses
    and phone numbers replaced. Returns per-table counts for the audit log;
    the summary holds no personal data.
    """
    email = (original_email or "").strip() or None
    email_lower = email.lower() if email else None
    phones = sorted({p.strip() for p in phones if p and p.strip()})
    placeholder = placeholder_email_for(user_id)
    summary: dict[str, int] = {}
    secrets = [value for value in (email, *phones) if value]

    async with _transaction() as w:
        # Authentication material and sessions.
        for table, sql in _PER_USER_DELETES.items():
            summary[table] = await w.optional(sql, (user_id,), table)

        # Staff records: anonymise, keeping the row so references resolve.
        for staff_id in staff_ids:
            summary["staff"] = summary.get("staff", 0) + await w.required(
                "UPDATE staff SET first_name = ?, last_name = ?, email = ?, mobile_phone = NULL "
                "WHERE id = ?",
                (ANON_FIRST_NAME, ANON_LAST_NAME, placeholder_staff_email_for(staff_id), staff_id),
            )
            await w.optional(
                "UPDATE staff SET street = NULL, city = NULL, state = NULL, postcode = NULL, "
                "offboarding_email_forward_to = NULL, offboarding_mailbox_grant_emails = NULL, "
                "offboarding_out_of_office = NULL, requested_by_name = NULL, "
                "requested_by_email = NULL WHERE id = ?",
                (staff_id,),
                "staff.extended",
            )

        # Ticket replies they wrote (by account or by email address).
        summary["ticket_replies"] = await w.required(
            "UPDATE ticket_replies SET author_display_name = ?, author_email = ? WHERE author_id = ?",
            (ANON_DISPLAY_NAME, placeholder, user_id),
        )
        if email_lower:
            summary["ticket_replies"] += await w.required(
                "UPDATE ticket_replies SET author_display_name = ?, author_email = ? "
                "WHERE LOWER(author_email) = ?",
                (ANON_DISPLAY_NAME, placeholder, email_lower),
            )

        # Redact the email address and phone numbers in their own replies and
        # in every reply and description on tickets they requested.
        ticket_marks = _placeholders(ticket_ids)
        for secret in secrets:
            await w.required(
                "UPDATE ticket_replies SET body = REPLACE(body, ?, ?) WHERE author_id = ?",
                (secret, REDACTED, user_id),
            )
            if ticket_ids:
                await w.required(
                    "UPDATE ticket_replies SET body = REPLACE(body, ?, ?) "  # nosec B608 - only "?" placeholders are joined in
                    "WHERE ticket_id IN (" + ticket_marks + ")",
                    (secret, REDACTED, *ticket_ids),
                )
                await w.required(
                    "UPDATE tickets SET description = REPLACE(description, ?, ?) "  # nosec B608 - only "?" placeholders are joined in
                    "WHERE id IN (" + ticket_marks + ") AND description IS NOT NULL",
                    (secret, REDACTED, *ticket_ids),
                )
                await w.optional(
                    "UPDATE tickets SET ai_summary = REPLACE(ai_summary, ?, ?) "  # nosec B608 - only "?" placeholders are joined in
                    "WHERE id IN (" + ticket_marks + ") AND ai_summary IS NOT NULL",
                    (secret, REDACTED, *ticket_ids),
                    "tickets.ai_summary",
                )
        summary["tickets"] = len(ticket_ids)

        # Email tracking events for emails they sent or were sent on their tickets.
        summary["email_tracking_events"] = await w.optional(
            "DELETE FROM email_tracking_events WHERE tracking_id IN ("
            "SELECT email_tracking_id FROM ticket_replies WHERE author_id = ? "
            "AND email_tracking_id IS NOT NULL)",
            (user_id,),
            "email_tracking_events.author",
        )
        if ticket_ids:
            summary["email_tracking_events"] += await w.optional(
                "DELETE FROM email_tracking_events WHERE tracking_id IN ("  # nosec B608 - only "?" placeholders are joined in
                "SELECT email_tracking_id FROM ticket_replies WHERE ticket_id IN (" + ticket_marks + ") "
                "AND email_tracking_id IS NOT NULL)",
                tuple(ticket_ids),
                "email_tracking_events.tickets",
            )

        # Chat messages: redact the body, then replace the sender's names.
        # sender_matrix_id is NOT NULL, so it gets a placeholder value.
        for secret in secrets:
            await w.optional(
                "UPDATE chat_messages SET body = REPLACE(body, ?, ?) "
                "WHERE sender_user_id = ? AND body IS NOT NULL",
                (secret, REDACTED, user_id),
                "chat_messages.redact",
            )
        summary["chat_messages"] = await w.optional(
            "UPDATE chat_messages SET sender_matrix_id = ?, sender_display_name = ? "
            "WHERE sender_user_id = ?",
            (placeholder, ANON_DISPLAY_NAME, user_id),
            "chat_messages.sender",
        )

        # SMS: the sender number is NOT NULL and unique per day, so remove the links.
        if phones:
            phone_marks = _placeholders(phones)
            summary["sms_ticket_links"] = await w.optional(
                "DELETE FROM sms_ticket_links WHERE from_number IN (" + phone_marks + ") "  # nosec B608 - only "?" placeholders are joined in
                "OR from_number_normalized IN (" + phone_marks + ")",
                tuple(phones) + tuple(phones),
                "sms_ticket_links",
            )

        # Call recordings and their transcripts; voicemail attachments.
        if call_recording_ids:
            summary["call_recordings"] = await w.optional(
                "DELETE FROM call_recordings WHERE id IN (" + _placeholders(call_recording_ids) + ")",  # nosec B608 - only "?" placeholders are joined in
                tuple(call_recording_ids),
                "call_recordings",
            )
        if attachment_ids:
            summary["ticket_attachments"] = await w.optional(
                "DELETE FROM ticket_attachments WHERE id IN (" + _placeholders(attachment_ids) + ")",  # nosec B608 - only "?" placeholders are joined in
                tuple(attachment_ids),
                "ticket_attachments",
            )

        # The account itself.
        summary["users"] = await w.required(
            "UPDATE users SET first_name = ?, last_name = ?, email = ?, password_hash = ?, "
            "mobile_phone = NULL, email_signature = NULL, booking_link_url = NULL, "
            "matrix_user_id = NULL, passkey_user_handle = NULL, is_active = 0 WHERE id = ?",
            (ANON_FIRST_NAME, ANON_LAST_NAME, placeholder, UNUSABLE_PASSWORD_HASH, user_id),
        )
        # Keep the anonymised account's content out of the AI index.
        await w.optional(
            "UPDATE users SET ai_opt_out = 1 WHERE id = ?", (user_id,), "users.ai_opt_out"
        )

    return summary
