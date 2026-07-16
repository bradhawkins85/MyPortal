"""Local invoice generation service.

Generates invoices from company recurring invoice items and billable tickets,
storing them locally in MyPortal (no external accounting system required).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from loguru import logger

from app.repositories import companies as company_repo
from app.repositories import invoice_lines as invoice_lines_repo
from app.repositories import invoices as invoice_repo
from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import ticket_expenses as expenses_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as users_repo
from app.services import modules as modules_service
from app.services import xero as xero_service


DEFAULT_XERO_LINE_ITEM_TEMPLATE = "Ticket {ticket_id}: {ticket_subject} {labour_suffix} ({labour_duration})"


def _env_xero_line_item_template() -> str:
    return str(os.getenv("XERO_LINE_ITEM_TEMPLATE", "")).strip()


def _minutes_to_hours(minutes: int) -> Decimal:
    return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _coerce_minutes(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


async def _get_xero_line_item_template() -> str:
    env_template = _env_xero_line_item_template()
    if env_template:
        return env_template

    try:
        settings = await modules_service.get_module_settings("xero") or {}
    except RuntimeError:
        settings = {}
    return str(settings.get("line_item_description_template") or "").strip() or DEFAULT_XERO_LINE_ITEM_TEMPLATE


def _strip_empty_description_segments(description: str) -> str:
    """Clean up punctuation left by invoice template placeholders removed for expenses."""

    cleaned = re.sub(r"\s*\(\s*\)", "", description)
    cleaned = re.sub(r"\s+-\s*(?=$|\n)", "", cleaned)
    cleaned = re.sub(r"\s+·\s*(?=$|\n)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(" -·")


def _build_expense_line_description(
    template: str,
    ticket: dict[str, Any],
    expenses: list[dict[str, Any]],
    *,
    requester_name: str = "",
    requester_email: str = "",
) -> str:
    """Build an expense invoice description without time or amount details."""

    expense_template = template
    for placeholder in (
        "{labour_duration}",
        "{labour_minutes}",
        "{labour_hours}",
        "{billable_minutes}",
        "{non_billable_minutes}",
    ):
        expense_template = expense_template.replace(placeholder, "")

    base_description = _build_ticket_line_description(
        expense_template,
        ticket,
        {"minutes": 0, "code": None, "name": "Expenses", "rate": None},
        0,
        billable_minutes=0,
        requester_name=requester_name,
        requester_email=requester_email,
    )
    base_description = _strip_empty_description_segments(base_description)

    expense_descriptions = [
        str(expense.get("description") or "Expense").strip() or "Expense"
        for expense in expenses
    ]
    if not expense_descriptions:
        return base_description
    if len(expense_descriptions) == 1:
        return f"{base_description} - {expense_descriptions[0]}" if base_description else expense_descriptions[0]
    expenses_text = "\n".join(expense_descriptions)
    return f"{base_description}\n{expenses_text}" if base_description else expenses_text


def _build_ticket_line_description(
    template: str,
    ticket: dict[str, Any],
    labour_group: dict[str, Any],
    minutes: int,
    *,
    billable_minutes: int,
    non_billable_minutes: int = 0,
    requester_name: str = "",
    requester_email: str = "",
) -> str:
    return xero_service._format_line_description(
        template,
        ticket,
        labour_group,
        minutes,
        billable_minutes=billable_minutes,
        non_billable_minutes=non_billable_minutes,
        requester_name=requester_name,
        requester_email=requester_email,
    )


async def resolve_ticket_requester(ticket: dict[str, Any]) -> tuple[str, str]:
    """Resolve requester display fields for invoice template substitutions."""

    return await xero_service.resolve_ticket_requester(ticket)


async def _generate_invoice_number() -> str:
    """Generate the next invoice number in the format INV-YYYYMM-NNNN."""
    now = datetime.now(timezone.utc)
    prefix = now.strftime("INV-%Y%m-")
    seq = await invoice_repo.get_max_invoice_seq(prefix)
    return f"{prefix}{seq + 1:04d}"


async def _get_xero_rate_lookup_credentials() -> tuple[str | None, str | None]:
    """Return Xero tenant/access token when item-price lookup is available.

    Recurring invoice items can intentionally omit a local price override so
    Xero remains the source of truth for product pricing. Rate lookup is best
    effort: invoice generation should still work when Xero is disabled or
    temporarily unavailable, but callers that can reach Xero should pass these
    credentials into the recurring line builder.
    """

    try:
        module = await modules_service.get_module("xero", redact=False)
    except Exception as exc:
        logger.warning("Failed to load Xero module for recurring item price lookup", error=str(exc))
        return None, None

    if not module or not module.get("enabled"):
        return None, None

    settings = dict(module.get("settings") or {})
    try:
        credentials = await modules_service.get_xero_credentials() or {}
    except Exception as exc:
        logger.warning("Failed to load Xero credentials for recurring item price lookup", error=str(exc))
        credentials = {}

    tenant_id = str(credentials.get("tenant_id") or settings.get("tenant_id") or "").strip()
    if not tenant_id:
        return None, None

    try:
        access_token = await modules_service.acquire_xero_access_token()
    except Exception as exc:
        logger.warning("Failed to acquire Xero access token for recurring item price lookup", error=str(exc))
        return None, None

    return tenant_id, access_token


async def generate_invoice(company_id: int) -> dict[str, Any]:
    """Generate a local invoice for the given company.

    Builds invoice line items from:
    - Company recurring invoice items
    - Billable tickets with unbilled time entries

    The generated invoice is stored locally in MyPortal and accessible
    via the /invoices page.

    Args:
        company_id: The company to generate an invoice for.

    Returns:
        A dictionary with the result status and details.
    """
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        return {
            "status": "skipped",
            "reason": "Company not found",
            "company_id": company_id,
        }

    # Build context for template substitution (device counts etc.)
    context = await xero_service.build_invoice_context(company_id)

    tenant_id, access_token = await _get_xero_rate_lookup_credentials()

    # Build recurring invoice items. When Xero is configured, pass credentials
    # so items without a local price override use their Xero sales price.
    recurring_line_items = await xero_service.build_recurring_invoice_items(
        company_id,
        tax_type=None,
        context=context,
        tenant_id=tenant_id,
        access_token=access_token,
        include_metadata=True,
    )

    line_item_template = await _get_xero_line_item_template()

    # Build ticket line items for billable tickets
    ticket_line_items: list[dict[str, Any]] = []
    tickets_context: list[dict[str, Any]] = []
    ticket_numbers: list[str] = []
    billable_tickets_found = 0

    # Fetch all tickets for the company and find unbilled ones
    try:
        all_tickets = await tickets_repo.list_tickets(company_id=company_id, limit=1000)
    except Exception as exc:
        logger.warning(
            "Failed to fetch tickets for invoice generation",
            company_id=company_id,
            error=str(exc),
        )
        all_tickets = []

    for ticket in all_tickets:
        ticket_id = ticket.get("id")
        if not ticket_id:
            continue

        unbilled_reply_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
        unbilled_expenses = await expenses_repo.list_expenses(ticket_id, unbilled_only=True)
        expense_total = sum((_to_decimal(expense.get("amount")) or Decimal("0")) for expense in unbilled_expenses)
        if not unbilled_reply_ids and expense_total <= 0:
            continue

        billable_tickets_found += 1
        all_replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
        unbilled_replies = [r for r in all_replies if r.get("id") in unbilled_reply_ids]

        # Group by labour type
        labour_map: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        billable_minutes = 0

        for reply in unbilled_replies:
            minutes = _coerce_minutes(reply.get("minutes_spent"))
            if minutes <= 0:
                continue
            if not reply.get("is_billable"):
                continue
            billable_minutes += minutes
            labour_code = str(reply.get("labour_type_code") or "").strip() or None
            labour_name = str(reply.get("labour_type_name") or "").strip() or None
            labour_rate = reply.get("labour_type_rate")
            key = (labour_code, labour_name)
            bucket = labour_map.get(key)
            if not bucket:
                bucket = {
                    "minutes": 0,
                    "code": labour_code,
                    "name": labour_name,
                    "rate": labour_rate,
                }
                labour_map[key] = bucket
            bucket["minutes"] += minutes

        labour_groups = list(labour_map.values())
        requester_name, requester_email = await resolve_ticket_requester(ticket)
        for group in labour_groups:
            group_minutes = int(group.get("minutes") or 0)
            if group_minutes <= 0:
                continue
            hours_decimal = _minutes_to_hours(group_minutes)
            labour_name = str(group.get("name") or "").strip()
            labour_code = str(group.get("code") or "").strip()
            description = _build_ticket_line_description(
                line_item_template,
                ticket,
                group,
                group_minutes,
                billable_minutes=billable_minutes,
                requester_name=requester_name,
                requester_email=requester_email,
            )

            local_rate = group.get("rate")
            rate: Decimal
            if local_rate is not None:
                rate = _to_decimal(local_rate) or Decimal("0")
            else:
                rate = Decimal("0")

            line_item: dict[str, Any] = {
                "Description": description,
                "Quantity": float(hours_decimal),
                "UnitAmount": float(rate),
                "ItemCode": labour_code,
            }
            ticket_line_items.append(line_item)

        tickets_context.append(
            {
                "id": ticket_id,
                "subject": ticket.get("subject"),
                "status": ticket.get("status"),
                "billable_minutes": billable_minutes,
                "labour_groups": labour_groups,
                "requester_name": requester_name,
                "requester_email": requester_email,
                "expense_ids": [int(expense["id"]) for expense in unbilled_expenses if expense.get("id")],
            }
        )

        if expense_total > 0:
            description = _build_expense_line_description(
                line_item_template,
                ticket,
                unbilled_expenses,
                requester_name=requester_name,
                requester_email=requester_email,
            )
            ticket_line_items.append({
                "Description": description,
                "Quantity": 1.0,
                "UnitAmount": float(expense_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "ItemCode": "",
                "MyPortalTicketExpenseIds": [int(expense["id"]) for expense in unbilled_expenses if expense.get("id")],
            })

        ticket_num = str(ticket_id)
        if ticket_num not in ticket_numbers:
            ticket_numbers.append(ticket_num)

    combined_line_items = recurring_line_items + ticket_line_items

    if not combined_line_items:
        logger.info(
            "No active recurring invoice items or billable tickets for company",
            company_id=company_id,
            billable_tickets_found=billable_tickets_found,
        )
        return {
            "status": "skipped",
            "reason": "No active recurring invoice items or billable tickets",
            "company_id": company_id,
            "billable_tickets_found": billable_tickets_found,
        }

    # Calculate total amount
    total_amount = Decimal("0.00")
    for item in combined_line_items:
        qty = Decimal(str(item.get("Quantity") or 0))
        unit = Decimal(str(item.get("UnitAmount") or 0))
        total_amount += (qty * unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Generate invoice number
    invoice_number = await _generate_invoice_number()

    # Determine due date (30 days from today)
    due_date = date.today() + timedelta(days=30)

    # Create the invoice record
    try:
        invoice = await invoice_repo.create_invoice(
            company_id=company_id,
            invoice_number=invoice_number,
            amount=total_amount,
            due_date=due_date,
            status="draft",
        )
    except Exception as exc:
        logger.error(
            "Failed to create local invoice",
            company_id=company_id,
            invoice_number=invoice_number,
            error=str(exc),
        )
        return {
            "status": "error",
            "reason": "Failed to create invoice record",
            "error": str(exc),
            "company_id": company_id,
        }

    invoice_id = invoice["id"]

    # Create invoice line records
    for item in combined_line_items:
        qty = Decimal(str(item.get("Quantity") or 1))
        unit = Decimal(str(item.get("UnitAmount") or 0))
        line_amount = (qty * unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        description = str(item.get("Description") or "").strip() or None
        product_code = str(item.get("ItemCode") or "").strip() or None
        try:
            await invoice_lines_repo.create_invoice_line(
                invoice_id=invoice_id,
                description=description,
                quantity=qty.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
                unit_amount=unit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                amount=line_amount,
                product_code=product_code,
            )
        except Exception as exc:
            logger.error(
                "Failed to create invoice line",
                invoice_id=invoice_id,
                error=str(exc),
            )

    recurring_item_ids = [
        int(item["MyPortalRecurringItemId"])
        for item in recurring_line_items
        if item.get("MyPortalRecurringItemId")
    ]
    now = datetime.now(timezone.utc)
    try:
        await recurring_items_repo.mark_recurring_invoice_items_billed(recurring_item_ids, billed_at=now)
    except Exception as exc:
        logger.error(
            "Failed to update recurring invoice item billing timestamps",
            company_id=company_id,
            invoice_id=invoice_id,
            error=str(exc),
        )

    # Mark time entries as billed and update ticket statuses
    billed_count = 0

    for ticket_ctx in tickets_context:
        ticket_id = ticket_ctx.get("id")
        if not ticket_id:
            continue

        unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
        replies = await tickets_repo.list_replies(ticket_id, include_internal=True)

        for reply in replies:
            reply_id = reply.get("id")
            if reply_id not in unbilled_ids:
                continue
            if not reply.get("is_billable"):
                continue
            minutes = reply.get("minutes_spent")
            if not minutes or minutes <= 0:
                continue
            labour_type_id = reply.get("labour_type_id")
            try:
                await billed_time_repo.create_billed_time_entry(
                    ticket_id=ticket_id,
                    reply_id=reply_id,
                    xero_invoice_number=invoice_number,
                    minutes_billed=minutes,
                    labour_type_id=labour_type_id,
                )
                billed_count += 1
            except Exception as exc:
                logger.error(
                    "Failed to record billed time entry",
                    ticket_id=ticket_id,
                    reply_id=reply_id,
                    error=str(exc),
                )

        try:
            await expenses_repo.mark_expenses_billed(list(ticket_ctx.get("expense_ids") or []), invoice_number=invoice_number, billed_at=now)
        except Exception as exc:
            logger.error("Failed to mark ticket expenses billed", ticket_id=ticket_id, error=str(exc))

        # Update ticket: mark as billed and move to the configured invoiced status.
        invoiced_status = xero_service.resolve_invoiced_ticket_status()
        try:
            await tickets_repo.update_ticket(
                ticket_id,
                xero_invoice_number=invoice_number,
                billed_at=now,
                status=invoiced_status,
                closed_at=now,
            )
        except Exception as exc:
            logger.error(
                "Failed to update ticket billing status",
                ticket_id=ticket_id,
                error=str(exc),
            )

    logger.info(
        "Generated local invoice",
        company_id=company_id,
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        total_amount=str(total_amount),
        line_items=len(combined_line_items),
        tickets_billed=len(tickets_context),
        time_entries_recorded=billed_count,
    )

    return {
        "status": "succeeded",
        "company_id": company_id,
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "total_amount": str(total_amount),
        "line_items": len(combined_line_items),
        "recurring_items": len(recurring_line_items),
        "ticket_items": len(ticket_line_items),
        "tickets_billed": len(tickets_context),
        "time_entries_recorded": billed_count,
    }
