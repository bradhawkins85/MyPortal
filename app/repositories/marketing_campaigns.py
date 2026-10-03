from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from app.core.database import db
from app.repositories import anonymisation as anonymisation_repo

_CAMPAIGN_FIELDS = (
    "name",
    "category",
    "subject",
    "message_template_id",
    "body_html",
    "sender_email",
    "sender_name",
    "reply_to",
    "audience",
    "business_hours_source",
    "scheduled_for",
)


def _deserialise(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def storage_datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def _normalise_campaign(row: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["id"] = int(record["id"])
    record["audience"] = _deserialise(record.get("audience")) or {}
    for key in ("scheduled_for", "queued_at", "completed_at", "cancelled_at"):
        record[key] = _aware(record.get(key))
    if record.get("message_template_id") is not None:
        record["message_template_id"] = int(record["message_template_id"])
    return record


def _normalise_recipient(row: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["id"] = int(record["id"])
    record["campaign_id"] = int(record["campaign_id"])
    for key in ("staff_id", "company_id", "reply_ticket_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    for key in (
        "send_after",
        "sent_at",
        "delivered_at",
        "first_opened_at",
        "first_clicked_at",
        "bounced_at",
        "replied_at",
    ):
        if key in record:
            record[key] = _aware(record.get(key))
    return record


def _campaign_params(fields: Mapping[str, Any]) -> list[Any]:
    params: list[Any] = []
    for key in _CAMPAIGN_FIELDS:
        value = fields.get(key)
        if key == "audience":
            value = json.dumps(value or {})
        elif key == "scheduled_for":
            value = storage_datetime(value)
        params.append(value)
    return params


async def list_campaigns() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT c.*, "
        "COUNT(r.id) AS recipient_count, "
        "SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END) AS sent_count, "
        "SUM(CASE WHEN r.first_opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened_count, "
        "SUM(CASE WHEN r.reply_ticket_id IS NOT NULL THEN 1 ELSE 0 END) AS replied_count "
        "FROM marketing_campaigns c "
        "LEFT JOIN marketing_campaign_recipients r ON r.campaign_id = c.id "
        "GROUP BY c.id ORDER BY c.created_at DESC, c.id DESC"
    )
    campaigns = []
    for row in rows:
        record = _normalise_campaign(row)
        for key in ("recipient_count", "sent_count", "opened_count", "replied_count"):
            record[key] = int(row.get(key) or 0)
        campaigns.append(record)
    return campaigns


async def get_campaign(campaign_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM marketing_campaigns WHERE id = %s", (campaign_id,))
    return _normalise_campaign(row) if row else None


async def create_campaign(fields: Mapping[str, Any], *, created_by: int | None) -> int:
    columns = ", ".join(_CAMPAIGN_FIELDS)
    return await db.execute_returning_lastrowid(
        "INSERT INTO marketing_campaigns (" + columns + ", created_by, status) "  # nosec B608
        "VALUES (" + _placeholders(_CAMPAIGN_FIELDS) + ", %s, 'draft')",
        tuple(_campaign_params(fields) + [created_by]),
    )


async def update_campaign(campaign_id: int, fields: Mapping[str, Any]) -> None:
    assignments = ", ".join(key + " = %s" for key in _CAMPAIGN_FIELDS)
    await db.execute(
        "UPDATE marketing_campaigns SET " + assignments + " WHERE id = %s AND status = 'draft'",  # nosec B608
        tuple(_campaign_params(fields) + [campaign_id]),
    )


async def delete_campaign(campaign_id: int) -> None:
    await db.execute(
        "DELETE FROM marketing_campaigns WHERE id = %s AND status IN ('draft', 'cancelled', 'completed')",
        (campaign_id,),
    )


async def mark_campaign_sending(campaign_id: int, *, queued_at: datetime) -> bool:
    return (
        await db.execute_rowcount(
            "UPDATE marketing_campaigns SET status = 'sending', queued_at = %s "
            "WHERE id = %s AND status = 'draft'",
            (storage_datetime(queued_at), campaign_id),
        )
        > 0
    )


async def cancel_campaign(campaign_id: int, *, cancelled_at: datetime) -> bool:
    changed = await db.execute_rowcount(
        "UPDATE marketing_campaigns SET status = 'cancelled', cancelled_at = %s "
        "WHERE id = %s AND status = 'sending'",
        (storage_datetime(cancelled_at), campaign_id),
    )
    if changed:
        await db.execute(
            "UPDATE marketing_campaign_recipients SET status = 'cancelled' "
            "WHERE campaign_id = %s AND status = 'pending'",
            (campaign_id,),
        )
    return changed > 0


async def complete_finished_campaigns(now: datetime) -> None:
    await db.execute(
        "UPDATE marketing_campaigns SET status = 'completed', completed_at = %s "
        "WHERE status = 'sending' AND NOT EXISTS ("
        "SELECT 1 FROM marketing_campaign_recipients r "
        "WHERE r.campaign_id = marketing_campaigns.id AND r.status IN ('pending', 'sending'))",
        (storage_datetime(now),),
    )


async def insert_recipients(campaign_id: int, recipients: Iterable[Mapping[str, Any]]) -> None:
    rows = [
        (
            campaign_id,
            recipient.get("staff_id"),
            recipient.get("company_id"),
            recipient["email"],
            recipient.get("name"),
            recipient["token"],
            recipient.get("status") or "pending",
            recipient.get("skip_reason"),
            storage_datetime(recipient.get("send_after")),
        )
        for recipient in recipients
    ]
    if not rows:
        return
    await db.execute_many(
        "INSERT INTO marketing_campaign_recipients "
        "(campaign_id, staff_id, company_id, email, name, token, status, skip_reason, send_after) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        rows,
    )


async def list_recipients(campaign_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT r.*, co.name AS company_name, t.ticket_number AS reply_ticket_number "
        "FROM marketing_campaign_recipients r "
        "LEFT JOIN companies co ON co.id = r.company_id "
        "LEFT JOIN tickets t ON t.id = r.reply_ticket_id "
        "WHERE r.campaign_id = %s ORDER BY co.name, r.email",
        (campaign_id,),
    )
    return [_normalise_recipient(row) for row in rows]


async def recipient_stats(campaign_id: int) -> dict[str, int]:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent, "
        "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
        "SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped, "
        "SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered, "
        "SUM(CASE WHEN first_opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened, "
        "SUM(CASE WHEN first_clicked_at IS NOT NULL THEN 1 ELSE 0 END) AS clicked, "
        "SUM(CASE WHEN bounced_at IS NOT NULL THEN 1 ELSE 0 END) AS bounced, "
        "SUM(CASE WHEN reply_ticket_id IS NOT NULL THEN 1 ELSE 0 END) AS replied "
        "FROM marketing_campaign_recipients WHERE campaign_id = %s",
        (campaign_id,),
    )
    return {key: int((row or {}).get(key) or 0) for key in (
        "total", "pending", "sent", "failed", "skipped", "delivered", "opened", "clicked", "bounced", "replied",
    )}


async def list_due_recipients(now: datetime, *, limit: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT r.* FROM marketing_campaign_recipients r "
        "JOIN marketing_campaigns c ON c.id = r.campaign_id "
        "WHERE c.status = 'sending' AND r.status = 'pending' "
        "AND (r.send_after IS NULL OR r.send_after <= %s) "
        "ORDER BY r.send_after, r.id LIMIT %s",
        (storage_datetime(now), int(limit)),
    )
    return [_normalise_recipient(row) for row in rows]


async def claim_recipient(recipient_id: int) -> bool:
    return (
        await db.execute_rowcount(
            "UPDATE marketing_campaign_recipients SET status = 'sending' "
            "WHERE id = %s AND status = 'pending'",
            (recipient_id,),
        )
        > 0
    )


async def defer_recipient(recipient_id: int, send_after: datetime) -> None:
    await db.execute(
        "UPDATE marketing_campaign_recipients SET status = 'pending', send_after = %s WHERE id = %s",
        (storage_datetime(send_after), recipient_id),
    )


async def skip_recipient(recipient_id: int, reason: str) -> None:
    await db.execute(
        "UPDATE marketing_campaign_recipients SET status = 'skipped', skip_reason = %s WHERE id = %s",
        (reason, recipient_id),
    )


async def mark_recipient_sent(
    recipient_id: int,
    *,
    sent_at: datetime,
    subject: str,
    message_id: str,
    smtp2go_message_id: str | None,
    provider: str | None,
) -> None:
    await db.execute(
        "UPDATE marketing_campaign_recipients SET status = 'sent', sent_at = %s, subject_rendered = %s, "
        "message_id = %s, smtp2go_message_id = %s, provider = %s, error_message = NULL WHERE id = %s",
        (storage_datetime(sent_at), subject[:255], message_id, smtp2go_message_id, provider, recipient_id),
    )


async def mark_recipient_failed(recipient_id: int, error: str) -> None:
    await db.execute(
        "UPDATE marketing_campaign_recipients SET status = 'failed', error_message = %s WHERE id = %s",
        (error[:2000], recipient_id),
    )


async def get_recipient_by_token(token: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT r.*, c.name AS campaign_name, c.category AS campaign_category "
        "FROM marketing_campaign_recipients r JOIN marketing_campaigns c ON c.id = r.campaign_id "
        "WHERE r.token = %s LIMIT 1",
        (token,),
    )
    return _normalise_recipient(row) if row else None


async def get_recipient_by_smtp2go_message_id(message_id: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM marketing_campaign_recipients WHERE smtp2go_message_id = %s LIMIT 1",
        (message_id,),
    )
    return _normalise_recipient(row) if row else None


async def list_recent_sent_to(email: str, since: datetime) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT r.*, c.name AS campaign_name FROM marketing_campaign_recipients r "
        "JOIN marketing_campaigns c ON c.id = r.campaign_id "
        "WHERE r.email = %s AND r.status = 'sent' AND r.sent_at >= %s "
        "ORDER BY r.sent_at DESC LIMIT 25",
        (email, storage_datetime(since)),
    )
    return [_normalise_recipient(row) for row in rows]


async def record_engagement(recipient_id: int, event_type: str, occurred_at: datetime) -> None:
    moment = storage_datetime(occurred_at)
    if event_type == "delivered":
        sql = "UPDATE marketing_campaign_recipients SET delivered_at = COALESCE(delivered_at, %s) WHERE id = %s"
    elif event_type == "open":
        sql = (
            "UPDATE marketing_campaign_recipients SET first_opened_at = COALESCE(first_opened_at, %s), "
            "open_count = open_count + 1 WHERE id = %s"
        )
    elif event_type == "click":
        sql = (
            "UPDATE marketing_campaign_recipients SET first_clicked_at = COALESCE(first_clicked_at, %s), "
            "click_count = click_count + 1 WHERE id = %s"
        )
    elif event_type in ("bounce", "rejected"):
        sql = "UPDATE marketing_campaign_recipients SET bounced_at = COALESCE(bounced_at, %s) WHERE id = %s"
    else:
        return
    await db.execute(sql, (moment, recipient_id))


async def link_reply_ticket(recipient_id: int, ticket_id: int, replied_at: datetime) -> None:
    await db.execute(
        "UPDATE marketing_campaign_recipients SET reply_ticket_id = %s, replied_at = %s WHERE id = %s",
        (ticket_id, storage_datetime(replied_at), recipient_id),
    )


async def touch_replied(recipient_id: int, replied_at: datetime) -> None:
    await db.execute(
        "UPDATE marketing_campaign_recipients SET replied_at = %s WHERE id = %s",
        (storage_datetime(replied_at), recipient_id),
    )


async def list_opted_out(emails: Sequence[str], category: str) -> set[str]:
    if not emails:
        return set()
    rows = await db.fetch_all(
        "SELECT email FROM marketing_email_opt_outs WHERE category = %s AND email IN ("
        + _placeholders(emails)  # nosec B608
        + ")",
        tuple([category, *emails]),
    )
    opted_out = {str(row["email"]).lower() for row in rows}
    return opted_out | await _anonymised_emails(emails)


async def _anonymised_emails(emails: Sequence[str]) -> set[str]:
    """Addresses of anonymised accounts, matched by their stored one-way hash.

    This keeps an anonymised person out of future campaigns even if their
    opt-out row is later removed.
    """
    try:
        hashes = await anonymisation_repo.list_anonymised_email_hashes(emails)
    except Exception:  # pragma: no cover - table missing on an old schema
        return set()
    return {
        str(email).lower()
        for email in emails
        if anonymisation_repo.email_hash(email) in hashes
    }


async def add_opt_out(email: str, category: str, campaign_id: int | None) -> None:
    existing = await db.fetch_one(
        "SELECT id FROM marketing_email_opt_outs WHERE email = %s AND category = %s",
        (email, category),
    )
    if existing:
        return
    await db.execute(
        "INSERT INTO marketing_email_opt_outs (email, category, campaign_id) VALUES (%s, %s, %s)",
        (email, category, campaign_id),
    )


async def remove_opt_out(email: str, category: str) -> None:
    await db.execute(
        "DELETE FROM marketing_email_opt_outs WHERE email = %s AND category = %s",
        (email, category),
    )


async def list_opt_outs(limit: int = 200, *, company_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """Return recent opt-outs, optionally only for contacts at ``company_ids``."""

    sql = (
        "SELECT o.*, c.name AS campaign_name FROM marketing_email_opt_outs o "
        "LEFT JOIN marketing_campaigns c ON c.id = o.campaign_id"
    )
    params: list[Any] = []
    if company_ids is not None:
        params = sorted(int(company_id) for company_id in company_ids)
        if not params:
            return []
        sql += (
            " WHERE EXISTS (SELECT 1 FROM staff s WHERE LOWER(s.email) = LOWER(o.email) "
            "AND s.company_id IN (" + _placeholders(params) + "))"  # nosec B608
        )
    rows = await db.fetch_all(sql + " ORDER BY o.created_at DESC LIMIT %s", tuple(params + [int(limit)]))
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Audience lookups
# ---------------------------------------------------------------------------


async def find_audience_contacts(audience: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return active staff that match the (already normalised) audience filters."""

    sql = (
        "SELECT s.id AS staff_id, s.company_id, s.first_name, s.last_name, s.email, "
        "s.job_title, s.department, co.name AS company_name, "
        "CASE WHEN EXISTS (SELECT 1 FROM billing_contacts bc WHERE bc.staff_id = s.id "
        "AND bc.company_id = s.company_id) THEN 1 ELSE 0 END AS is_billing_contact "
        "FROM staff s JOIN companies co ON co.id = s.company_id "
        "WHERE s.enabled = 1 AND COALESCE(s.is_ex_staff, 0) = 0 "
        "AND s.email IS NOT NULL AND s.email <> '' AND COALESCE(co.archived, 0) = 0"
    )
    params: list[Any] = []

    company_ids = list(audience.get("company_ids") or [])
    if audience.get("company_mode") == "selected":
        if not company_ids:
            return []
        sql += " AND s.company_id IN (" + _placeholders(company_ids) + ")"
        params.extend(company_ids)
    exclude_company_ids = list(audience.get("exclude_company_ids") or [])
    if exclude_company_ids:
        sql += " AND s.company_id NOT IN (" + _placeholders(exclude_company_ids) + ")"
        params.extend(exclude_company_ids)
    if audience.get("vip_only"):
        sql += " AND COALESCE(co.is_vip, 0) = 1"

    asset_field_ids = list(audience.get("asset_field_ids") or [])
    if asset_field_ids:
        sql += (
            " AND EXISTS (SELECT 1 FROM assets a JOIN asset_custom_field_values v ON v.asset_id = a.id "
            "WHERE a.company_id = s.company_id AND v.value_boolean = 1 "
            "AND v.field_definition_id IN (" + _placeholders(asset_field_ids) + "))"  # nosec B608
        )
        params.extend(asset_field_ids)

    product_ids = list(audience.get("product_ids") or [])
    if product_ids:
        sql += (
            " AND EXISTS (SELECT 1 FROM subscriptions sub WHERE sub.customer_id = s.company_id "
            "AND sub.status IN ('active', 'pending_renewal') "
            "AND sub.product_id IN (" + _placeholders(product_ids) + "))"  # nosec B608
        )
        params.extend(product_ids)

    if audience.get("contact_scope") == "billing":
        sql += (
            " AND EXISTS (SELECT 1 FROM billing_contacts bc2 WHERE bc2.staff_id = s.id "
            "AND bc2.company_id = s.company_id)"
        )

    for column, key in (("s.job_title", "job_titles"), ("s.department", "departments")):
        terms = list(audience.get(key) or [])
        if terms:
            sql += " AND (" + " OR ".join([column + " LIKE %s"] * len(terms)) + ")"
            params.extend("%" + term + "%" for term in terms)

    sql += " ORDER BY co.name, s.last_name, s.first_name, s.id"
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows]


async def find_staff_by_emails(emails: Sequence[str]) -> list[dict[str, Any]]:
    if not emails:
        return []
    rows = await db.fetch_all(
        "SELECT s.id AS staff_id, s.company_id, s.first_name, s.last_name, s.email, "
        "s.job_title, s.department, co.name AS company_name, 0 AS is_billing_contact "
        "FROM staff s JOIN companies co ON co.id = s.company_id "
        "WHERE s.enabled = 1 AND COALESCE(s.is_ex_staff, 0) = 0 AND LOWER(s.email) IN ("
        + _placeholders(emails)  # nosec B608
        + ") ORDER BY s.id",
        tuple(emails),
    )
    return [dict(row) for row in rows]


async def list_company_options(company_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, name FROM companies WHERE COALESCE(archived, 0) = 0"
    params: list[Any] = []
    if company_ids is not None:
        params = sorted(int(company_id) for company_id in company_ids)
        if not params:
            return []
        sql += " AND id IN (" + _placeholders(params) + ")"
    rows = await db.fetch_all(sql + " ORDER BY name", tuple(params))
    return [{"id": int(row["id"]), "name": str(row.get("name") or "")} for row in rows]


async def list_asset_field_options() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, name FROM asset_custom_field_definitions WHERE field_type = 'checkbox' "
        "ORDER BY display_order, name"
    )
    return [{"id": int(row["id"]), "name": str(row.get("name") or "")} for row in rows]


async def list_product_options() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT DISTINCT p.id, p.name FROM shop_products p "
        "JOIN subscriptions sub ON sub.product_id = p.id ORDER BY p.name"
    )
    return [{"id": int(row["id"]), "name": str(row.get("name") or "")} for row in rows]


async def get_contact(staff_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT s.id AS staff_id, s.company_id, s.first_name, s.last_name, s.email, "
        "s.job_title, s.department, co.name AS company_name "
        "FROM staff s LEFT JOIN companies co ON co.id = s.company_id WHERE s.id = %s",
        (staff_id,),
    )
    return dict(row) if row else None
