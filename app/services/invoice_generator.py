"""Local invoice generation service.

Generates invoices from company recurring invoice items and billable tickets,
storing them locally in MyPortal (no external accounting system required).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from loguru import logger

from app.repositories import companies as company_repo
from app.repositories import invoice_lines as invoice_lines_repo
from app.repositories import invoices as invoice_repo
from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import tickets as tickets_repo
from app.services import xero as xero_service


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


async def _generate_invoice_number() -> str:
    """Generate the next invoice number in the format INV-YYYYMM-NNNN."""
    now = datetime.now(timezone.utc)
    prefix = now.strftime("INV-%Y%m-")
    seq = await invoice_repo.get_max_invoice_seq(prefix)
    return f"{prefix}{seq + 1:04d}"


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

    # Build recurring invoice items (no Xero credentials needed)
    recurring_line_items = await xero_service.build_recurring_invoice_items(
        company_id,
        tax_type=None,
        context=context,
    )

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
        if not unbilled_reply_ids:
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

        if billable_minutes <= 0:
            continue

        labour_groups = list(labour_map.values())
        ticket_subject = str(ticket.get("subject") or "").strip()

        for group in labour_groups:
            group_minutes = int(group.get("minutes") or 0)
            if group_minutes <= 0:
                continue
            hours_decimal = _minutes_to_hours(group_minutes)
            labour_name = str(group.get("name") or "").strip()
            labour_code = str(group.get("code") or "").strip()

            if labour_name:
                description = f"Ticket #{ticket_id}: {ticket_subject} — {labour_name} ({hours_decimal}h)"
            else:
                description = f"Ticket #{ticket_id}: {ticket_subject} ({hours_decimal}h)"

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
            }
        )

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

    # Mark time entries as billed and update ticket statuses
    billed_count = 0
    now = datetime.now(timezone.utc)

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

        # Update ticket: mark as billed and close
        try:
            await tickets_repo.update_ticket(
                ticket_id,
                xero_invoice_number=invoice_number,
                billed_at=now,
                status="closed",
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
