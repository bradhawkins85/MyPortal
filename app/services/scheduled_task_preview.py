from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from app.repositories import companies as company_repo
from app.repositories import invoice_lines as invoice_lines_repo
from app.repositories import invoices as invoice_repo
from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import tickets as tickets_repo
from app.services import invoice_generator as invoice_generator_service
from app.services import modules as modules_service
from app.services import subscription_price_changes
from app.services import unbill_time_entries as unbill_time_entries_service
from app.services import xero as xero_service

PREVIEWABLE_COMMANDS = {
    "sync_to_xero",
    "sync_to_xero_auto_send",
    "generate_invoice",
    "unbill_time_entries",
    "send_price_change_notifications",
}


def _money(value: Any) -> str:
    try:
        return str(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return "0.00"


async def preview_task(task: Mapping[str, Any]) -> dict[str, Any]:
    command = str(task.get("command") or "").strip()
    if command not in PREVIEWABLE_COMMANDS:
        return {
            "status": "unsupported",
            "command": command,
            "summary": "Preview is not available for this scheduled task command.",
            "items": [],
        }
    if command in {"sync_to_xero", "sync_to_xero_auto_send", "generate_invoice", "unbill_time_entries"}:
        company_id = task.get("company_id")
        if not company_id and command != "unbill_time_entries":
            return {
                "status": "skipped",
                "command": command,
                "summary": "Company context is required before this task can run.",
                "items": [],
            }
        if command == "unbill_time_entries":
            return await unbill_time_entries_service.preview_unbill_time_entries(int(company_id) if company_id else None)
        company = await company_repo.get_company_by_id(int(company_id))
        if not company:
            return {
                "status": "skipped",
                "command": command,
                "company_id": int(company_id),
                "summary": "Company was not found.",
                "items": [],
            }
        if command == "generate_invoice":
            return await _preview_generate_invoice(int(company_id), company)
        return await _preview_sync_to_xero(int(company_id), company, auto_send=command == "sync_to_xero_auto_send")
    return await _preview_price_change_notifications()


async def _preview_sync_to_xero(company_id: int, company: Mapping[str, Any], *, auto_send: bool) -> dict[str, Any]:
    invoices = await invoice_repo.list_unsynced_company_invoices(company_id)
    items: list[dict[str, Any]] = []
    total = Decimal("0.00")
    for invoice in invoices:
        invoice_id = int(invoice.get("id") or 0)
        lines = await invoice_lines_repo.list_invoice_lines(invoice_id) if invoice_id else []
        amount = Decimal(str(invoice.get("amount") or 0))
        total += amount
        items.append({
            "type": "invoice",
            "id": invoice_id,
            "label": str(invoice.get("invoice_number") or f"Invoice #{invoice_id}"),
            "amount": _money(amount),
            "lineCount": len(lines),
            "dueDate": invoice.get("due_date"),
            "action": "Authorise and send to Xero" if auto_send else "Create draft invoice in Xero",
        })
    return {
        "status": "ready" if items else "skipped",
        "command": "sync_to_xero_auto_send" if auto_send else "sync_to_xero",
        "company_id": company_id,
        "company_name": company.get("name") or f"Company #{company_id}",
        "summary": (
            f"{len(items)} unsynchronised MyPortal invoice(s) will be uploaded to Xero"
            + (" and sent automatically." if auto_send else " as draft invoices.")
            if items else "No unsynchronised MyPortal invoices were found."
        ),
        "totals": {"invoiceCount": len(items), "amount": _money(total)},
        "items": items,
    }


async def _preview_generate_invoice(company_id: int, company: Mapping[str, Any]) -> dict[str, Any]:
    line_item_template = await invoice_generator_service._get_xero_line_item_template()
    context = await xero_service.build_invoice_context(company_id)
    recurring_items = await xero_service.build_recurring_invoice_items(company_id, tax_type=None, context=context)
    tickets = await tickets_repo.list_tickets(company_id=company_id, limit=1000)
    ticket_items: list[dict[str, Any]] = []
    billable_reply_count = 0
    for ticket in tickets:
        ticket_id = ticket.get("id")
        if not ticket_id:
            continue
        unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
        if not unbilled_ids:
            continue
        replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
        minutes = sum(invoice_generator_service._coerce_minutes(r.get("minutes_spent")) for r in replies if r.get("id") in unbilled_ids and r.get("is_billable"))
        if minutes <= 0:
            continue
        billable_reply_count += len([r for r in replies if r.get("id") in unbilled_ids and r.get("is_billable")])
        labour_map: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        for reply in replies:
            if reply.get("id") not in unbilled_ids or not reply.get("is_billable"):
                continue
            reply_minutes = invoice_generator_service._coerce_minutes(reply.get("minutes_spent"))
            if reply_minutes <= 0:
                continue
            labour_code = str(reply.get("labour_type_code") or "").strip() or None
            labour_name = str(reply.get("labour_type_name") or "").strip() or None
            labour_rate = reply.get("labour_type_rate")
            key = (labour_code, labour_name)
            bucket = labour_map.get(key)
            if not bucket:
                bucket = {"minutes": 0, "code": labour_code, "name": labour_name, "rate": labour_rate}
                labour_map[key] = bucket
            bucket["minutes"] += reply_minutes
        requester_name, requester_email = await invoice_generator_service.resolve_ticket_requester(dict(ticket))
        for group in labour_map.values():
            group_minutes = int(group.get("minutes") or 0)
            if group_minutes <= 0:
                continue
            hours = invoice_generator_service._minutes_to_hours(group_minutes)
            description = invoice_generator_service._build_ticket_line_description(
                line_item_template,
                ticket,
                group,
                group_minutes,
                billable_minutes=minutes,
                requester_name=requester_name,
                requester_email=requester_email,
            )
            unit_amount = invoice_generator_service._to_decimal(group.get("rate")) or Decimal("0")
            labour_code = str(group.get("code") or "").strip()
            ticket_item: dict[str, Any] = {
                "type": "ticket",
                "id": int(ticket_id),
                "label": description,
                "minutes": group_minutes,
                "hours": str(hours),
                "xeroDescription": description,
                "xeroQuantity": str(hours),
                "xeroUnitAmount": _money(unit_amount),
                "action": "Add billable ticket time using this Xero line item format",
            }
            if labour_code:
                ticket_item["xeroItemCode"] = labour_code
            ticket_items.append(ticket_item)
    recurring_total = sum(Decimal(str(i.get("Quantity") or 0)) * Decimal(str(i.get("UnitAmount") or 0)) for i in recurring_items)
    return {
        "status": "ready" if recurring_items or ticket_items else "skipped",
        "command": "generate_invoice",
        "company_id": company_id,
        "company_name": company.get("name") or f"Company #{company_id}",
        "summary": "A draft MyPortal invoice will be generated." if recurring_items or ticket_items else "No active recurring invoice items or billable tickets were found.",
        "totals": {"recurringLineCount": len(recurring_items), "ticketLineCount": len(ticket_items), "billableReplyCount": billable_reply_count, "recurringAmount": _money(recurring_total)},
        "items": [{"type": "recurring", "label": i.get("Description") or i.get("ItemCode") or "Recurring item", "amount": _money(Decimal(str(i.get("Quantity") or 0)) * Decimal(str(i.get("UnitAmount") or 0))), "action": "Add recurring line to the generated invoice"} for i in recurring_items] + ticket_items,
    }


async def _preview_price_change_notifications() -> dict[str, Any]:
    products = await subscription_price_changes.get_products_with_pending_price_changes()
    items = [{
        "type": "product",
        "id": int(product.get("id") or 0),
        "label": product.get("name") or product.get("sku") or f"Product #{product.get('id')}",
        "category": product.get("category_name"),
        "effectiveDate": product.get("price_change_date"),
        "action": "Notify subscribed billing contacts about the scheduled price change",
    } for product in products]
    return {
        "status": "ready" if items else "skipped",
        "command": "send_price_change_notifications",
        "summary": f"{len(items)} product(s) have pending price change notifications." if items else "No pending price change notifications were found.",
        "totals": {"productCount": len(items)},
        "items": items,
    }
