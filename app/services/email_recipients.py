"""Per-recipient email delivery tracking.

Each ticket reply email send produces one row per To/CC/BCC recipient in the
``ticket_reply_email_recipients`` table. The existing aggregate columns on
``ticket_replies`` continue to drive the single delivery-status badge; this
module powers the click-through popup that breaks delivery down per
recipient.

Public API:

* :func:`record_recipients` — insert one row per recipient at send time.
* :func:`get_recipients_for_reply` — list recipients for a given reply.
* :func:`get_recipient_count_map` — bulk lookup for view-model rendering.
* :func:`update_recipient_event` — apply a webhook event to the matching
  recipient row, lazy-creating it if necessary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from loguru import logger

from app.core.database import db


_VALID_ROLES = {"to", "cc", "bcc"}

# Mapping of internal event type -> column on the recipient row that should be
# stamped (using COALESCE so first-occurrence wins for monotonic events).
_EVENT_COLUMNS: dict[str, str] = {
    "processed": "email_processed_at",
    "delivered": "email_delivered_at",
    "open": "email_opened_at",
    "bounce": "email_bounced_at",
    "rejected": "email_rejected_at",
    "spam": "email_spam_at",
}


def _normalise_email(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    # Address could be in "Name <addr@example.com>" form; if so, extract the
    # part inside the angle brackets.
    if "<" in text and ">" in text:
        start = text.find("<")
        end = text.find(">", start)
        if end > start:
            inner = text[start + 1 : end].strip()
            if inner:
                text = inner
    return text


def _normalise_role(value: Any) -> str:
    if not value:
        return "to"
    text = str(value).strip().lower()
    return text if text in _VALID_ROLES else "to"


async def record_recipients(
    *,
    reply_id: int,
    tracking_id: str | None,
    smtp2go_message_id: str | None,
    to: Iterable[str] | None = None,
    cc: Iterable[str] | None = None,
    bcc: Iterable[str] | None = None,
    names_by_email: Mapping[str, str] | None = None,
    sent_at: datetime | None = None,
) -> int:
    """Insert one row per recipient for a ticket reply send.

    Idempotent: a recipient that already exists for the same
    ``(reply_id, recipient_email, recipient_role)`` is left untouched
    (we don't want to clobber webhook-driven status updates). When
    ``smtp2go_message_id`` / ``tracking_id`` is provided and the existing
    row has no value for those columns, they are filled in.

    Returns the number of recipient rows that were inserted.
    """
    if reply_id is None:
        return 0

    try:
        reply_id_int = int(reply_id)
    except (TypeError, ValueError):
        logger.warning("record_recipients called with non-integer reply_id", reply_id=reply_id)
        return 0

    name_lookup: dict[str, str] = {}
    if names_by_email:
        for raw_email, name in names_by_email.items():
            normalised = _normalise_email(raw_email)
            if normalised and name:
                name_lookup[normalised] = str(name).strip()

    seen: set[tuple[str, str]] = set()

    def _expand(addresses: Iterable[str] | None, role: str) -> list[tuple[str, str, str | None]]:
        out: list[tuple[str, str, str | None]] = []
        if not addresses:
            return out
        for raw in addresses:
            email = _normalise_email(raw)
            if not email:
                continue
            key = (email, role)
            if key in seen:
                continue
            seen.add(key)
            out.append((email, role, name_lookup.get(email)))
        return out

    rows: list[tuple[str, str, str | None]] = []
    rows.extend(_expand(to, "to"))
    rows.extend(_expand(cc, "cc"))
    rows.extend(_expand(bcc, "bcc"))
    if not rows:
        return 0

    inserted = 0
    timestamp = sent_at or datetime.now(timezone.utc)

    for recipient_email, role, recipient_name in rows:
        try:
            existing = await db.fetch_one(
                """
                SELECT id, tracking_id, smtp2go_message_id
                FROM ticket_reply_email_recipients
                WHERE ticket_reply_id = :reply_id
                  AND recipient_email = :email
                  AND recipient_role = :role
                LIMIT 1
                """,
                {
                    "reply_id": reply_id_int,
                    "email": recipient_email,
                    "role": role,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.opt(exception=True).error(
                "Failed to look up existing recipient row",
                reply_id=reply_id_int,
                recipient=recipient_email,
                error=str(exc),
            )
            continue

        if existing:
            # Backfill tracking identifiers on a previously-stored row when
            # they become known (e.g. SMTP2Go message id arrives after the
            # initial send write).
            updates: dict[str, Any] = {}
            if tracking_id and not existing.get("tracking_id"):
                updates["tracking_id"] = tracking_id
            if smtp2go_message_id and not existing.get("smtp2go_message_id"):
                updates["smtp2go_message_id"] = smtp2go_message_id
            if updates:
                set_clauses = ", ".join(f"{col} = :{col}" for col in updates)
                params = {**updates, "id": existing["id"]}
                try:
                    await db.execute(
                        f"UPDATE ticket_reply_email_recipients SET {set_clauses} WHERE id = :id",
                        params,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.opt(exception=True).error(
                        "Failed to backfill recipient tracking ids",
                        recipient_id=existing["id"],
                        error=str(exc),
                    )
            continue

        try:
            await db.execute(
                """
                INSERT INTO ticket_reply_email_recipients (
                    ticket_reply_id, recipient_email, recipient_role, recipient_name,
                    tracking_id, smtp2go_message_id, email_sent_at,
                    created_at, updated_at
                ) VALUES (
                    :reply_id, :email, :role, :name,
                    :tracking_id, :smtp2go_message_id, :sent_at,
                    :created_at, :updated_at
                )
                """,
                {
                    "reply_id": reply_id_int,
                    "email": recipient_email,
                    "role": role,
                    "name": recipient_name,
                    "tracking_id": tracking_id,
                    "smtp2go_message_id": smtp2go_message_id,
                    "sent_at": timestamp,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            inserted += 1
        except Exception as exc:  # pragma: no cover - defensive
            logger.opt(exception=True).error(
                "Failed to insert recipient row",
                reply_id=reply_id_int,
                recipient=recipient_email,
                error=str(exc),
            )

    if inserted:
        logger.info(
            "Recorded email recipients for ticket reply",
            reply_id=reply_id_int,
            count=inserted,
            tracking_id=tracking_id,
            smtp2go_message_id=smtp2go_message_id,
        )
    return inserted


async def get_recipients_for_reply(reply_id: int) -> list[dict[str, Any]]:
    """Return all recipient rows for a ticket reply, oldest first."""
    try:
        rows = await db.fetch_all(
            """
            SELECT id, ticket_reply_id, recipient_email, recipient_role, recipient_name,
                   tracking_id, smtp2go_message_id,
                   email_sent_at, email_processed_at, email_delivered_at,
                   email_opened_at, email_open_count,
                   email_bounced_at, email_rejected_at, email_spam_at,
                   last_event_at, last_event_type, last_event_detail,
                   created_at, updated_at
            FROM ticket_reply_email_recipients
            WHERE ticket_reply_id = :reply_id
            ORDER BY id ASC
            """,
            {"reply_id": int(reply_id)},
        )
    except Exception as exc:
        logger.opt(exception=True).error(
            "Failed to load recipient rows",
            reply_id=reply_id,
            error=str(exc),
        )
        return []

    return [dict(row) for row in rows]


async def get_recipient_count_map(reply_ids: Iterable[int]) -> dict[int, int]:
    """Return ``{reply_id: count}`` for the given reply ids.

    Replies with zero recipients in the new table are simply absent from the
    returned mapping (callers should default to 0).
    """
    ids = []
    for value in reply_ids or ():
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    counts: dict[int, int] = {}
    # Iterate one query per id rather than building an IN clause to stay within
    # the simple key-style parameter binding the rest of the codebase uses.
    for reply_id in set(ids):
        try:
            row = await db.fetch_one(
                """
                SELECT COUNT(*) AS recipient_count
                FROM ticket_reply_email_recipients
                WHERE ticket_reply_id = :reply_id
                """,
                {"reply_id": reply_id},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.opt(exception=True).debug(
                "Failed to count recipient rows",
                reply_id=reply_id,
                error=str(exc),
            )
            continue
        if row:
            try:
                counts[reply_id] = int(row.get("recipient_count") or 0)
            except (TypeError, ValueError):
                counts[reply_id] = 0
    return counts


async def _find_recipient_row(
    *,
    smtp2go_message_id: str | None,
    tracking_id: str | None,
    recipient_email: str,
) -> dict[str, Any] | None:
    """Look up a recipient row by SMTP2Go message id or tracking id + email."""
    if smtp2go_message_id:
        row = await db.fetch_one(
            """
            SELECT id, ticket_reply_id, recipient_email, recipient_role,
                   tracking_id, smtp2go_message_id, email_open_count
            FROM ticket_reply_email_recipients
            WHERE smtp2go_message_id = :smtp2go_message_id
              AND recipient_email = :email
            ORDER BY id ASC
            LIMIT 1
            """,
            {"smtp2go_message_id": smtp2go_message_id, "email": recipient_email},
        )
        if row:
            return dict(row)
    if tracking_id:
        row = await db.fetch_one(
            """
            SELECT id, ticket_reply_id, recipient_email, recipient_role,
                   tracking_id, smtp2go_message_id, email_open_count
            FROM ticket_reply_email_recipients
            WHERE tracking_id = :tracking_id
              AND recipient_email = :email
            ORDER BY id ASC
            LIMIT 1
            """,
            {"tracking_id": tracking_id, "email": recipient_email},
        )
        if row:
            return dict(row)
    return None


async def update_recipient_event(
    *,
    event_type: str,
    occurred_at: datetime,
    recipient_email: str | None,
    smtp2go_message_id: str | None = None,
    tracking_id: str | None = None,
    ticket_reply_id: int | None = None,
    detail: str | None = None,
) -> int | None:
    """Apply a webhook event to the matching per-recipient row.

    Returns the recipient row id that was created or updated, or ``None`` if
    the event could not be associated with a recipient (e.g. webhook missing
    the recipient address and no fallback ``ticket_reply_id``).
    """
    normalised_email = _normalise_email(recipient_email)
    if not normalised_email:
        # Without a recipient address we cannot meaningfully update a single
        # row; skip rather than mutating every recipient on the message.
        return None

    column = _EVENT_COLUMNS.get(event_type)

    row = await _find_recipient_row(
        smtp2go_message_id=smtp2go_message_id,
        tracking_id=tracking_id,
        recipient_email=normalised_email,
    )

    if not row and ticket_reply_id is not None:
        # Lazy-create a recipient row when an event arrives before
        # record_recipients has been called (or for an address that wasn't
        # in the original send list). Default the role to 'to'; webhook
        # data does not distinguish CC/BCC reliably.
        try:
            await db.execute(
                """
                INSERT INTO ticket_reply_email_recipients (
                    ticket_reply_id, recipient_email, recipient_role,
                    tracking_id, smtp2go_message_id, created_at, updated_at
                ) VALUES (
                    :reply_id, :email, 'to',
                    :tracking_id, :smtp2go_message_id, :created_at, :updated_at
                )
                """,
                {
                    "reply_id": int(ticket_reply_id),
                    "email": normalised_email,
                    "tracking_id": tracking_id,
                    "smtp2go_message_id": smtp2go_message_id,
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.opt(exception=True).error(
                "Failed to lazy-create recipient row from webhook",
                reply_id=ticket_reply_id,
                recipient=normalised_email,
                error=str(exc),
            )
            return None
        row = await _find_recipient_row(
            smtp2go_message_id=smtp2go_message_id,
            tracking_id=tracking_id,
            recipient_email=normalised_email,
        )

    if not row:
        return None

    set_clauses: list[str] = ["updated_at = :updated_at", "last_event_at = :last_event_at",
                              "last_event_type = :last_event_type"]
    params: dict[str, Any] = {
        "id": row["id"],
        "updated_at": occurred_at,
        "last_event_at": occurred_at,
        "last_event_type": event_type,
    }

    if column:
        # COALESCE keeps the first-seen timestamp for monotonic events
        # (delivered/bounce/etc.). For 'open' we still preserve the first
        # open while bumping the counter below.
        set_clauses.append(f"{column} = COALESCE({column}, :event_ts)")
        params["event_ts"] = occurred_at

    if event_type == "open":
        # Use existing count + 1 rather than a SQL expression for SQLite
        # compatibility with the dialect translation in the migration runner
        # tests; this is fine because webhook processing is serialised per
        # event.
        try:
            current = int(row.get("email_open_count") or 0)
        except (TypeError, ValueError):
            current = 0
        set_clauses.append("email_open_count = :open_count")
        params["open_count"] = current + 1

    # SMTP2Go provides a 'processed' event before the message is fully sent;
    # also stamp email_sent_at so the recipient row mirrors the aggregate
    # column behaviour (which sets email_sent_at on processed).
    if event_type == "processed":
        set_clauses.append("email_sent_at = COALESCE(email_sent_at, :event_ts)")
        params.setdefault("event_ts", occurred_at)

    if detail is not None:
        set_clauses.append("last_event_detail = :detail")
        params["detail"] = str(detail)[:65000]

    sql = f"UPDATE ticket_reply_email_recipients SET {', '.join(set_clauses)} WHERE id = :id"
    try:
        await db.execute(sql, params)
    except Exception as exc:  # pragma: no cover - defensive
        logger.opt(exception=True).error(
            "Failed to update recipient row",
            recipient_id=row.get("id"),
            event_type=event_type,
            error=str(exc),
        )
        return None

    return int(row["id"]) if row.get("id") is not None else None


def compute_status(row: Mapping[str, Any]) -> str:
    """Compute the per-recipient status label.

    Priority: ``bounced > spam > rejected > opened > delivered > processed >
    sent > pending``.
    """
    if row.get("email_bounced_at"):
        return "bounced"
    if row.get("email_spam_at"):
        return "spam"
    if row.get("email_rejected_at"):
        return "rejected"
    if row.get("email_opened_at"):
        return "opened"
    if row.get("email_delivered_at"):
        return "delivered"
    if row.get("email_processed_at"):
        return "processed"
    if row.get("email_sent_at"):
        return "sent"
    return "pending"
