"""Service for managing subscription renewals and scheduled invoices."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from typing import Any

from email_validator import EmailNotValidError, validate_email
from loguru import logger

from app.repositories import billing_contacts as billing_contacts_repo
from app.repositories import companies as company_repo
from app.repositories import licenses as license_repo
from app.repositories import scheduled_invoices as invoices_repo
from app.repositories import shop as shop_repo
from app.repositories import subscription_change_requests as change_requests_repo
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import tickets as tickets_repo
from app.services import email as email_service
from app.services import invoice_generator
from app.services import shop as shop_service
from app.services import tickets as tickets_service
from app.services import xero as xero_service


_REMINDER_WINDOW_DAYS = 60
_INVOICE_WINDOW_DAYS = 30
_DEFAULT_CURRENCY_LABEL = "Default billing currency"


def _as_money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:  # pragma: no cover - defensive parsing
        return Decimal("0.00")


def _format_money(amount: Decimal, currency: str = _DEFAULT_CURRENCY_LABEL) -> str:
    return f"{currency} {amount.quantize(Decimal('0.01'))}"


def _billing_contact_name(contact: dict[str, Any] | None) -> str:
    if not contact:
        return "Billing contact"
    first_name = str(contact.get("first_name") or "").strip()
    last_name = str(contact.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    return full_name or str(contact.get("email") or "Billing contact").strip() or "Billing contact"


def _ticket_external_reference(company_id: int, renewal_date: date) -> str:
    return f"subscription-renewal:{company_id}:{renewal_date.isoformat()}"


def _reply_external_reference(company_id: int, renewal_date: date) -> str:
    return f"subscription-renewal-reply:{company_id}:{renewal_date.isoformat()}"


def _term_days_for_product(product: dict[str, Any]) -> int:
    billing_plan = shop_service.get_subscription_billing_plan(product or {})
    commitment_type = billing_plan[0] if billing_plan else str(product.get("commitment_type") or "").strip().lower()
    if commitment_type == "monthly":
        return 30
    return 365


def _term_label(product: dict[str, Any]) -> str:
    billing_plan = shop_service.get_subscription_billing_plan(product or {})
    if billing_plan == ("monthly", "monthly"):
        return "Monthly commitment · monthly payment"
    if billing_plan == ("annual", "monthly"):
        return "Annual commitment · monthly payment"
    if billing_plan == ("annual", "annual"):
        return "Annual commitment · annual payment"
    if str(product.get("commitment_type") or "").strip().lower() == "monthly":
        return "Monthly commitment"
    if str(product.get("commitment_type") or "").strip().lower() == "annual":
        return "Annual commitment"
    return "Annual commitment"


def _calculate_renewal_quantity(
    subscription: dict[str, Any],
    pending_changes: list[dict[str, Any]],
) -> int:
    pending_decrease = sum(
        int(change.get("quantity_change") or 0)
        for change in pending_changes
        if change.get("change_type") == "decrease"
        and str(change.get("status") or "pending").strip().lower() == "pending"
    )
    return max(0, int(subscription.get("quantity") or 0) - pending_decrease)


def _license_lookup_keys(subscription: dict[str, Any], product: dict[str, Any]) -> list[str]:
    values = [
        product.get("vendor_sku"),
        product.get("sku"),
        product.get("name"),
        subscription.get("product_name"),
    ]
    keys: list[str] = []
    for value in values:
        cleaned = str(value or "").strip().casefold()
        if cleaned and cleaned not in keys:
            keys.append(cleaned)
    return keys


def _format_assigned_user(staff: dict[str, Any]) -> str:
    first_name = str(staff.get("first_name") or "").strip()
    last_name = str(staff.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    email = str(staff.get("email") or "").strip()
    if full_name and email:
        return f"{full_name} <{email}>"
    return full_name or email or f"Staff #{staff.get('id')}"


def _build_renewal_message(
    *,
    company_name: str,
    renewal_date: date,
    billing_contact: dict[str, Any] | None,
    renewal_items: list[dict[str, Any]],
) -> str:
    total_cost = sum(
        (item["line_total"] for item in renewal_items),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    lines = [
        f"Subscription renewal reminder for {company_name}",
        f"Billing contact: {_billing_contact_name(billing_contact)}",
        f"Renewal date: {renewal_date.isoformat()}",
        "",
    ]
    for item in renewal_items:
        assigned_users = item["assigned_users"] or ["None assigned"]
        lines.extend(
            [
                f"Subscription: {item['subscription_name']}",
                f"License: {item['license_name']}",
                f"Description: {item['description'] or 'No description provided'}",
                f"Renewing quantity: {item['renewal_quantity']}",
                f"Assigned users: {', '.join(assigned_users)}",
            ]
        )
        if item["extra_assigned_user_count"] > 0:
            lines.append(
                "Additional currently assigned users above renewing quantity: "
                f"{item['extra_assigned_user_count']}"
            )
        lines.extend(
            [
                f"Unassigned licenses: {item['unassigned_quantity']}",
                f"Renewal term: {item['renewal_term']}",
                f"Unit price: {_format_money(item['unit_price'], item['currency'])}",
                f"Line total: {_format_money(item['line_total'], item['currency'])}",
                "",
            ]
        )
    lines.extend(
        [
            f"Total renewal cost: {_format_money(total_cost)}",
            "",
            "This renewal notice was generated automatically.",
        ]
    )
    return "\n".join(lines).strip()


def _build_html_from_text(message: str) -> str:
    return "<br>".join(escape(line) for line in message.splitlines())


async def _select_billing_contact(
    company_id: int,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    contacts = await billing_contacts_repo.list_billing_contacts_for_company(company_id)
    if not contacts:
        return None, None, "No billing contact is configured for this company."

    first_contact = contacts[0]
    invalid_reasons: list[str] = []
    for contact in contacts:
        raw_email = str(contact.get("email") or "").strip()
        if not raw_email:
            invalid_reasons.append(
                f"{_billing_contact_name(contact)} is missing an email address."
            )
            continue
        try:
            normalised = validate_email(
                raw_email, check_deliverability=False
            ).normalized
        except EmailNotValidError as exc:
            invalid_reasons.append(
                f"{_billing_contact_name(contact)} has an invalid email address: {exc}."
            )
            continue
        return contact, normalised, None

    return first_contact, None, invalid_reasons[0] if invalid_reasons else (
        "No valid billing contact email address is configured for this company."
    )


async def _ensure_scheduled_invoice(
    company_id: int,
    renewal_date: date,
) -> dict[str, Any]:
    existing = await invoices_repo.get_scheduled_invoice_by_customer_and_date(
        company_id, renewal_date
    )
    if existing:
        if existing.get("status") == "canceled" and existing.get("invoice_id") is None:
            return await invoices_repo.patch_scheduled_invoice(
                int(existing["id"]),
                status="scheduled",
            )
        return existing
    return await invoices_repo.create_scheduled_invoice(
        customer_id=company_id,
        scheduled_for_date=renewal_date,
        status="scheduled",
    )


async def _build_group_renewal_items(
    company_id: int,
    subscriptions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pending_changes = await change_requests_repo.list_pending_changes_for_subscriptions(
        [str(subscription["id"]) for subscription in subscriptions]
    )
    company_licenses = await license_repo.list_company_licenses(company_id)
    staff_by_license = await license_repo.list_staff_by_license_for_company(company_id)

    licenses_by_key: dict[str, dict[str, Any]] = {}
    for record in company_licenses:
        for value in (
            record.get("platform"),
            record.get("display_name"),
            record.get("name"),
        ):
            key = str(value or "").strip().casefold()
            if key and key not in licenses_by_key:
                licenses_by_key[key] = record

    renewal_items: list[dict[str, Any]] = []
    for subscription in subscriptions:
        product = await shop_repo.get_product_by_id(int(subscription["product_id"])) or {}
        renewal_quantity = _calculate_renewal_quantity(
            subscription,
            pending_changes.get(str(subscription["id"]), []),
        )
        if renewal_quantity <= 0:
            continue

        matched_license: dict[str, Any] | None = None
        for key in _license_lookup_keys(subscription, product):
            matched_license = licenses_by_key.get(key)
            if matched_license:
                break

        assigned_staff = list(
            staff_by_license.get(int(matched_license["id"]), [])
            if matched_license and matched_license.get("id") is not None
            else []
        )
        renewing_assigned_staff = assigned_staff[:renewal_quantity]
        extra_assigned_user_count = max(len(assigned_staff) - renewal_quantity, 0)
        unassigned_quantity = max(renewal_quantity - len(renewing_assigned_staff), 0)
        unit_price = _as_money(subscription.get("unit_price"))
        term_days = _term_days_for_product(product)
        next_term_start = subscription["end_date"] + timedelta(days=1)
        next_term_end = next_term_start + timedelta(days=term_days - 1)
        subscription_name = str(
            subscription.get("product_name")
            or product.get("name")
            or f"Subscription {subscription['id']}"
        ).strip()
        license_name = str(
            (matched_license or {}).get("display_name")
            or (matched_license or {}).get("name")
            or product.get("name")
            or subscription_name
        ).strip()
        description = str(
            product.get("invoice_description")
            or product.get("description")
            or subscription.get("category_name")
            or ""
        ).strip()
        product_code = str(product.get("sku") or product.get("vendor_sku") or "").strip()
        line_total = (unit_price * renewal_quantity).quantize(Decimal("0.01"))
        renewal_items.append(
            {
                "subscription_id": str(subscription["id"]),
                "product_id": int(subscription["product_id"]),
                "subscription_name": subscription_name,
                "license_name": license_name,
                "description": description,
                "renewal_quantity": renewal_quantity,
                "assigned_users": [
                    _format_assigned_user(staff) for staff in renewing_assigned_staff
                ],
                "extra_assigned_user_count": extra_assigned_user_count,
                "unassigned_quantity": unassigned_quantity,
                "renewal_date": subscription["end_date"],
                "renewal_term": _term_label(product),
                "unit_price": unit_price,
                "currency": _DEFAULT_CURRENCY_LABEL,
                "line_total": line_total,
                "product_code": product_code,
                "term_start": next_term_start,
                "term_end": next_term_end,
            }
        )

    renewal_items.sort(key=lambda item: (item["subscription_name"], item["license_name"]))
    return renewal_items


async def _sync_scheduled_invoice_lines(
    scheduled_invoice_id: int,
    renewal_items: list[dict[str, Any]],
) -> None:
    await invoices_repo.delete_invoice_lines(scheduled_invoice_id)
    for item in renewal_items:
        await invoices_repo.add_invoice_line(
            invoice_id=scheduled_invoice_id,
            subscription_id=item["subscription_id"],
            product_id=item["product_id"],
            term_start=item["term_start"],
            term_end=item["term_end"],
            price=item["unit_price"],
        )


async def _mark_pending_renewal(subscriptions: list[dict[str, Any]]) -> None:
    for subscription in subscriptions:
        if str(subscription.get("status") or "").strip().lower() == "active":
            await subscriptions_repo.update_subscription(
                str(subscription["id"]),
                status="pending_renewal",
            )


async def _record_issue(
    *,
    scheduled_invoice_id: int,
    field: str,
    message: str,
) -> None:
    await invoices_repo.patch_scheduled_invoice(
        scheduled_invoice_id,
        **{field: message},
    )


async def _process_reminder(
    *,
    company: dict[str, Any],
    renewal_date: date,
    scheduled_invoice: dict[str, Any],
    renewal_items: list[dict[str, Any]],
    issue_log: list[dict[str, Any]],
) -> bool:
    company_id = int(company["id"])
    billing_contact, billing_email, billing_error = await _select_billing_contact(
        company_id
    )
    if billing_contact is None:
        await _record_issue(
            scheduled_invoice_id=int(scheduled_invoice["id"]),
            field="reminder_error",
            message=billing_error or "No billing contact is configured for this company.",
        )
        issue_log.append(
            {
                "company_id": company_id,
                "renewal_date": renewal_date.isoformat(),
                "stage": "reminder",
                "message": billing_error or "No billing contact is configured for this company.",
            }
        )
        return False

    now = datetime.now(timezone.utc)
    company_name = str(company.get("name") or f"Company {company_id}")
    subject = f"Subscription renewal reminder for {company_name} ({renewal_date.isoformat()})"
    message = _build_renewal_message(
        company_name=company_name,
        renewal_date=renewal_date,
        billing_contact=billing_contact,
        renewal_items=renewal_items,
    )
    ticket_reference = _ticket_external_reference(company_id, renewal_date)
    ticket = await tickets_repo.get_ticket_by_external_reference(ticket_reference)
    if ticket is None:
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=message,
            requester_id=int(billing_contact["staff_id"]),
            requester_staff_id=None,
            company_id=company_id,
            assigned_user_id=None,
            priority="Medium",
            status="open",
            category="Billing",
            module_slug="subscriptions",
            external_reference=ticket_reference,
            send_creation_notification=False,
            trigger_automations=True,
        )

    patch_updates: dict[str, Any] = {
        "reminder_ticket_id": int(ticket["id"]),
        "reminder_error": None,
    }
    reply_reference = _reply_external_reference(company_id, renewal_date)
    reply = None
    if scheduled_invoice.get("reminder_reply_id") is not None:
        reply = {"id": int(scheduled_invoice["reminder_reply_id"])}
    else:
        reply = await tickets_repo.get_reply_by_external_reference(
            int(ticket["id"]),
            reply_reference,
        )
    if reply is None:
        reply = await tickets_repo.create_reply(
            ticket_id=int(ticket["id"]),
            author_id=None,
            body=message,
            is_internal=False,
            external_reference=reply_reference,
            author_display_name="MyPortal",
        )
    patch_updates["reminder_reply_id"] = int(reply["id"])
    patch_updates["reminder_sent_at"] = now

    if billing_email and scheduled_invoice.get("reminder_email_sent_at") is None:
        sent, _ = await email_service.send_email(
            subject=subject,
            recipients=[billing_email],
            html_body=_build_html_from_text(message),
            text_body=message,
        )
        if sent:
            patch_updates["reminder_email_sent_at"] = now
        else:
            billing_error = (
                "Reminder email delivery failed for the configured billing contact."
            )
    if billing_error:
        patch_updates["reminder_error"] = billing_error
        issue_log.append(
            {
                "company_id": company_id,
                "renewal_date": renewal_date.isoformat(),
                "stage": "reminder",
                "message": billing_error,
            }
        )

    await invoices_repo.patch_scheduled_invoice(
        int(scheduled_invoice["id"]),
        **patch_updates,
    )
    return True


def _build_invoice_line_items(renewal_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    line_items: list[dict[str, Any]] = []
    for item in renewal_items:
        line_items.append(
            {
                "Description": (
                    f"{item['subscription_name']} renewal - {item['renewal_term']} "
                    f"(renews {item['renewal_date'].isoformat()})"
                ),
                "Quantity": item["renewal_quantity"],
                "UnitAmount": float(item["unit_price"]),
                "ItemCode": item["product_code"],
            }
        )
    return line_items


async def _process_invoice(
    *,
    company: dict[str, Any],
    renewal_date: date,
    scheduled_invoice: dict[str, Any],
    renewal_items: list[dict[str, Any]],
    issue_log: list[dict[str, Any]],
) -> bool:
    company_id = int(company["id"])
    _billing_contact, billing_email, billing_error = await _select_billing_contact(
        company_id
    )
    if billing_email is None:
        await _record_issue(
            scheduled_invoice_id=int(scheduled_invoice["id"]),
            field="invoice_error",
            message=billing_error or "No valid billing contact email is configured for this company.",
        )
        issue_log.append(
            {
                "company_id": company_id,
                "renewal_date": renewal_date.isoformat(),
                "stage": "invoice",
                "message": billing_error or "No valid billing contact email is configured for this company.",
            }
        )
        return False

    result = await invoice_generator.generate_invoice(
        company_id,
        include_recurring_items=False,
        recurring_line_items=_build_invoice_line_items(renewal_items),
        include_ticket_items=False,
    )
    if result.get("status") != "succeeded":
        message = str(
            result.get("reason")
            or result.get("error")
            or "Renewal invoice generation failed."
        )
        await _record_issue(
            scheduled_invoice_id=int(scheduled_invoice["id"]),
            field="invoice_error",
            message=message,
        )
        issue_log.append(
            {
                "company_id": company_id,
                "renewal_date": renewal_date.isoformat(),
                "stage": "invoice",
                "message": message,
            }
        )
        return False

    company_settings = await company_repo.get_company_by_id(company_id) or {}
    auto_send = bool(company_settings.get("xero_auto_send_subscription_invoices", 1))
    xero_result = await xero_service.sync_invoice(
        int(result["invoice_id"]),
        auto_send=auto_send,
    )
    invoice_error = None
    xero_status = str(xero_result.get("status") or "").strip().lower()
    if xero_status not in {"succeeded"}:
        invoice_error = str(
            xero_result.get("reason")
            or xero_result.get("error")
            or "Invoice generated locally but delivery did not complete."
        )
        issue_log.append(
            {
                "company_id": company_id,
                "renewal_date": renewal_date.isoformat(),
                "stage": "invoice",
                "message": invoice_error,
            }
        )

    await invoices_repo.patch_scheduled_invoice(
        int(scheduled_invoice["id"]),
        status="issued",
        invoice_id=int(result["invoice_id"]),
        invoice_number=result.get("invoice_number"),
        invoice_sent_at=datetime.now(timezone.utc) if invoice_error is None else None,
        invoice_error=invoice_error,
    )
    return True


def build_renewal_forecast(
    subscriptions: list[dict[str, Any]],
    *,
    today: date,
    reminder_offsets: tuple[int, ...] = (60, 30, 7),
) -> dict[str, Any]:
    """Build a lightweight renewal forecast dashboard payload."""
    active_subscriptions = [
        sub for sub in subscriptions
        if sub.get("status") in {"active", "pending_renewal"}
        and isinstance(sub.get("end_date"), date)
    ]
    window_counts = {30: 0, 60: 0, 90: 0}
    projected_revenue_90 = Decimal("0.00")
    reminder_campaigns: list[dict[str, Any]] = []

    for window in window_counts:
        window_counts[window] = sum(
            1
            for sub in active_subscriptions
            if 0 <= (sub["end_date"] - today).days <= window
        )

    for sub in active_subscriptions:
        days_until_renewal = (sub["end_date"] - today).days
        if 0 <= days_until_renewal <= 90:
            projected_revenue_90 += _as_money(sub.get("unit_price")) * int(
                sub.get("quantity") or 0
            )

    for offset in reminder_offsets:
        due_ids = [
            sub["id"]
            for sub in active_subscriptions
            if (sub["end_date"] - today).days == offset
        ]
        reminder_campaigns.append(
            {
                "days_before": offset,
                "subscription_count": len(due_ids),
                "subscription_ids": due_ids,
            }
        )

    return {
        "renewing_in_30_days": window_counts[30],
        "renewing_in_60_days": window_counts[60],
        "renewing_in_90_days": window_counts[90],
        "projected_revenue_90": projected_revenue_90.quantize(Decimal("0.01")),
        "reminder_campaigns": reminder_campaigns,
    }


def build_churn_risk_report(
    subscriptions: list[dict[str, Any]],
    *,
    today: date,
    pending_changes_by_subscription: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build auditable churn-risk heuristics from renewal, usage, ticket, and payment signals."""
    pending_changes_by_subscription = pending_changes_by_subscription or {}
    at_risk: list[dict[str, Any]] = []

    for sub in subscriptions:
        end_date = sub.get("end_date")
        if not isinstance(end_date, date):
            continue

        score = 0
        reasons: list[str] = []
        evidence: dict[str, Any] = {}
        days_until_renewal = (end_date - today).days

        if not bool(sub.get("auto_renew")) and 0 <= days_until_renewal <= 30:
            score += 3
            reasons.append("Auto-renew is disabled inside the 30-day renewal window.")
        elif not bool(sub.get("auto_renew")):
            score += 1
            reasons.append("Auto-renew is disabled.")

        pending_decreases = [
            change for change in pending_changes_by_subscription.get(sub["id"], [])
            if change.get("change_type") == "decrease"
        ]
        if pending_decreases:
            score += 2
            reasons.append("Pending decrease requests indicate a planned contraction.")
            evidence["pending_decrease_count"] = len(pending_decreases)

        usage_ratio = sub.get("usage_ratio")
        if usage_ratio is not None and Decimal(str(usage_ratio)) < Decimal("0.50"):
            score += 2
            reasons.append("Recent usage is below 50% of allocated capacity.")
            evidence["usage_ratio"] = str(usage_ratio)

        open_ticket_count = int(sub.get("open_ticket_count") or 0)
        if open_ticket_count >= 3:
            score += 1
            reasons.append("Support ticket volume is elevated.")
            evidence["open_ticket_count"] = open_ticket_count

        payment_health = str(sub.get("payment_health") or "").strip().lower()
        if payment_health in {"overdue", "failed", "delinquent"}:
            score += 3
            reasons.append("Payments are overdue or failing.")
            evidence["payment_health"] = payment_health

        level = "none"
        if score >= 5:
            level = "high"
        elif score >= 3:
            level = "medium"
        elif score > 0:
            level = "low"

        item = {
            "subscription_id": sub["id"],
            "product_name": sub.get("product_name"),
            "score": score,
            "level": level,
            "days_until_renewal": days_until_renewal,
            "reasons": reasons,
            "evidence": evidence,
        }
        if level != "none":
            at_risk.append(item)

    at_risk.sort(
        key=lambda item: (
            -item["score"],
            item["days_until_renewal"],
            str(item.get("product_name") or ""),
        )
    )
    return {
        "high": sum(1 for item in at_risk if item["level"] == "high"),
        "medium": sum(1 for item in at_risk if item["level"] == "medium"),
        "low": sum(1 for item in at_risk if item["level"] == "low"),
        "total_at_risk": len(at_risk),
        "items": at_risk,
    }


async def create_renewal_invoices_for_date(target_date: date) -> dict[str, Any]:
    """Process 60-day renewal reminders and 30-day automated renewal invoices."""
    logger.info("Starting subscription renewal processing", target_date=target_date)
    subscriptions = await subscriptions_repo.list_subscriptions(
        end_before=target_date + timedelta(days=_REMINDER_WINDOW_DAYS + 1),
        end_after=target_date - timedelta(days=1),
    )
    eligible_subscriptions = [
        subscription
        for subscription in subscriptions
        if subscription.get("status") in {"active", "pending_renewal"}
        and bool(subscription.get("auto_renew", True))
        and isinstance(subscription.get("end_date"), date)
        and 0 <= (subscription["end_date"] - target_date).days <= _REMINDER_WINDOW_DAYS
    ]

    if not eligible_subscriptions:
        logger.info("No subscriptions due for renewal processing", target_date=target_date)
        return {
            "processed_count": 0,
            "reminder_count": 0,
            "invoice_count": 0,
            "customer_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "processed_subscription_ids": [],
            "issues": [],
        }

    grouped: dict[tuple[int, date], list[dict[str, Any]]] = defaultdict(list)
    for subscription in eligible_subscriptions:
        grouped[(int(subscription["customer_id"]), subscription["end_date"])].append(
            subscription
        )

    reminder_count = 0
    invoice_count = 0
    skipped_count = 0
    processed_subscription_ids: list[str] = []
    issues: list[dict[str, Any]] = []

    for (company_id, renewal_date), group_subscriptions in grouped.items():
        scheduled_invoice = await _ensure_scheduled_invoice(company_id, renewal_date)
        renewal_items = await _build_group_renewal_items(company_id, group_subscriptions)
        if not renewal_items:
            if (
                scheduled_invoice.get("status") != "issued"
                and scheduled_invoice.get("invoice_id") is None
            ):
                await invoices_repo.patch_scheduled_invoice(
                    int(scheduled_invoice["id"]),
                    status="canceled",
                    reminder_error=None,
                    invoice_error=None,
                )
            skipped_count += 1
            continue

        await _sync_scheduled_invoice_lines(int(scheduled_invoice["id"]), renewal_items)
        await _mark_pending_renewal(group_subscriptions)
        processed_subscription_ids.extend(
            str(subscription["id"]) for subscription in group_subscriptions
        )

        company = await company_repo.get_company_by_id(company_id) or {
            "id": company_id,
            "name": f"Company {company_id}",
        }
        days_until_renewal = (renewal_date - target_date).days
        group_changed = False

        if 0 <= days_until_renewal <= _REMINDER_WINDOW_DAYS and (
            scheduled_invoice.get("reminder_sent_at") is None
            or scheduled_invoice.get("reminder_email_sent_at") is None
        ):
            if await _process_reminder(
                company=company,
                renewal_date=renewal_date,
                scheduled_invoice=scheduled_invoice,
                renewal_items=renewal_items,
                issue_log=issues,
            ):
                reminder_count += 1
                group_changed = True
            scheduled_invoice = await invoices_repo.get_scheduled_invoice(
                int(scheduled_invoice["id"])
            ) or scheduled_invoice

        if 0 <= days_until_renewal <= _INVOICE_WINDOW_DAYS and scheduled_invoice.get(
            "invoice_id"
        ) is None:
            if await _process_invoice(
                company=company,
                renewal_date=renewal_date,
                scheduled_invoice=scheduled_invoice,
                renewal_items=renewal_items,
                issue_log=issues,
            ):
                invoice_count += 1
                group_changed = True

        if not group_changed:
            skipped_count += 1

    unique_processed_ids = sorted(set(processed_subscription_ids))
    unique_customers = {company_id for company_id, _renewal_date in grouped}
    logger.info(
        "Subscription renewal processing completed",
        target_date=target_date,
        processed_count=len(unique_processed_ids),
        reminder_count=reminder_count,
        invoice_count=invoice_count,
        customer_count=len(unique_customers),
        skipped_count=skipped_count,
        error_count=len(issues),
    )
    return {
        "processed_count": len(unique_processed_ids),
        "reminder_count": reminder_count,
        "invoice_count": invoice_count,
        "customer_count": len(unique_customers),
        "skipped_count": skipped_count,
        "error_count": len(issues),
        "processed_subscription_ids": unique_processed_ids,
        "issues": issues,
    }


async def get_next_scheduled_invoice_for_subscription(
    subscription_id: str,
) -> dict[str, Any] | None:
    """Get the next scheduled invoice for a subscription."""
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        return None

    invoice = await invoices_repo.get_scheduled_invoice_by_customer_and_date(
        subscription["customer_id"],
        subscription["end_date"],
    )

    return invoice
