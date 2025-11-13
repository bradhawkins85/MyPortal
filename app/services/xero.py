from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Mapping, MutableMapping, Sequence

import httpx
from loguru import logger

from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import tickets as tickets_repo
from app.services import modules as modules_service
from app.services import webhook_monitor

TicketFetcher = Callable[[int], Awaitable[Mapping[str, Any] | None]]
RepliesFetcher = Callable[[int], Awaitable[Sequence[Mapping[str, Any]] | None]]
CompanyFetcher = Callable[[int], Awaitable[Mapping[str, Any] | None]]
OrderSummaryFetcher = Callable[[str, int], Awaitable[Mapping[str, Any] | None]]
OrderItemsFetcher = Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]] | None]]


class _TemplateValues(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _normalise_status_filter(
    statuses: Sequence[Any] | str | None,
) -> set[str] | None:
    if statuses in (None, ""):
        return None

    candidates: list[Any]
    if isinstance(statuses, str):
        text = statuses.strip()
        if not text:
            return None
        parsed: Any
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            segments = [segment.strip() for segment in re.split(r"[,;\n]+", text) if segment.strip()]
            candidates = segments if segments else [text]
        else:
            if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
                candidates = list(parsed)
            else:
                candidates = [parsed]
    elif isinstance(statuses, Sequence):
        candidates = list(statuses)
    else:
        candidates = [statuses]

    filtered: set[str] = set()
    for status in candidates:
        text = str(status or "").strip().lower()
        if text:
            filtered.add(text)
    return filtered or None


def _collect_ticket_numbers(tickets: Sequence[Mapping[str, Any]]) -> list[str]:
    numbers: list[str] = []
    for ticket in tickets:
        identifier = ticket.get("id")
        if identifier is None:
            continue
        candidate = str(identifier)
        if candidate not in numbers:
            numbers.append(candidate)
    return numbers


def _build_reference(reference_prefix: str, ticket_numbers: Sequence[str]) -> str:
    reference_parts: list[str] = []
    prefix = reference_prefix.strip()
    if prefix:
        reference_parts.append(prefix)
    tickets_text = ", ".join(number for number in ticket_numbers if number)
    if tickets_text:
        reference_parts.append(f"Tickets {tickets_text}")
    return " — ".join(reference_parts)


def _format_line_description(
    template: str,
    ticket: Mapping[str, Any],
    labour: Mapping[str, Any] | None,
    minutes: int,
) -> str:
    safe_template = template.strip() or "Ticket {ticket_id}: {ticket_subject}{labour_suffix}"
    subject = str(ticket.get("subject") or "").strip()
    labour_name = str((labour or {}).get("name") or "").strip()
    labour_code = str((labour or {}).get("code") or "").strip()
    labour_minutes = max(0, int(minutes))
    labour_hours_decimal = _minutes_to_hours(labour_minutes) if labour_minutes else Decimal("0")
    values = _TemplateValues(
        ticket_id=ticket.get("id"),
        ticket_subject=subject,
        ticket_status=str(ticket.get("status") or "").strip(),
        labour_name=labour_name,
        labour_code=labour_code,
        labour_minutes=labour_minutes,
        labour_hours=float(_quantize(labour_hours_decimal)) if labour_minutes else 0.0,
        labour_suffix=f" · {labour_name}" if labour_name else "",
    )
    try:
        description = safe_template.format_map(values).strip()
    except Exception:  # pragma: no cover - defensive guardrail
        description = ""
    if not description:
        description = f"Ticket {ticket.get('id')}: {subject}".strip()
        if labour_name:
            description = f"{description} · {labour_name}" if description else labour_name
    return description


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _quantize(value: Decimal, places: str = "0.01") -> Decimal:
    quantiser = Decimal(places)
    return value.quantize(quantiser, rounding=ROUND_HALF_UP)


def _minutes_to_hours(minutes: int) -> Decimal:
    return _quantize(Decimal(minutes) / Decimal(60))


def _coerce_minutes(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


async def build_ticket_invoices(
    ticket_ids: Sequence[Any],
    *,
    hourly_rate: Decimal,
    account_code: str,
    tax_type: str | None,
    line_amount_type: str,
    reference_prefix: str,
    allowed_statuses: Sequence[str] | None = None,
    description_template: str | None = None,
    invoice_date: date | None = None,
    existing_invoice_map: MutableMapping[tuple[int, date], dict[str, Any]] | None = None,
    fetch_ticket: TicketFetcher,
    fetch_replies: RepliesFetcher,
    fetch_company: CompanyFetcher,
) -> list[dict[str, Any]]:
    """Construct invoice payloads for the provided ticket identifiers."""

    status_filter = _normalise_status_filter(allowed_statuses)
    line_item_template = (description_template or "").strip()
    invoice_day = invoice_date or date.today()
    invoice_lookup: MutableMapping[tuple[int, date], dict[str, Any]]
    if existing_invoice_map is None:
        invoice_lookup = {}
    else:
        invoice_lookup = existing_invoice_map

    unique_ticket_ids: list[int] = []
    for raw_identifier in ticket_ids:
        try:
            ticket_id = int(raw_identifier)
        except (TypeError, ValueError):
            continue
        if ticket_id <= 0 or ticket_id in unique_ticket_ids:
            continue
        unique_ticket_ids.append(ticket_id)

    if not unique_ticket_ids:
        return []

    tickets_by_company: dict[int, list[dict[str, Any]]] = {}
    for ticket_id in unique_ticket_ids:
        ticket = await fetch_ticket(ticket_id)
        if not ticket:
            continue
        raw_company_id = ticket.get("company_id")
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        ticket_status = str(ticket.get("status") or "").strip().lower()
        if status_filter is not None and ticket_status not in status_filter:
            continue

        replies = await fetch_replies(ticket_id) or []
        labour_map: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        billable_minutes = 0
        for reply in replies:
            minutes = _coerce_minutes(reply.get("minutes_spent"))
            if minutes <= 0:
                continue
            is_billable = reply.get("is_billable")
            if not is_billable:
                continue
            billable_minutes += minutes
            labour_code = str(reply.get("labour_type_code") or "").strip() or None
            labour_name = str(reply.get("labour_type_name") or "").strip() or None
            key = (labour_code, labour_name)
            bucket = labour_map.get(key)
            if not bucket:
                bucket = {"minutes": 0, "code": labour_code, "name": labour_name}
                labour_map[key] = bucket
            bucket["minutes"] += minutes
        if billable_minutes <= 0:
            continue

        entry = {
            "ticket": ticket,
            "billable_minutes": billable_minutes,
            "labour_groups": list(labour_map.values()) if labour_map else [],
        }
        tickets_by_company.setdefault(company_id, []).append(entry)

    invoices: list[dict[str, Any]] = []
    appended_invoices: set[int] = set()
    if not tickets_by_company:
        return invoices

    rate_decimal = _to_decimal(hourly_rate) or Decimal("0")
    if rate_decimal <= 0:
        logger.warning("Ticket invoice builder received a non-positive hourly rate")

    for company_id, entries in tickets_by_company.items():
        company_record = await fetch_company(company_id) or {}
        line_items: list[dict[str, Any]] = []
        context_tickets: list[dict[str, Any]] = []
        total_minutes = 0
        for item in entries:
            ticket = item.get("ticket") or {}
            ticket_minutes = int(item.get("billable_minutes") or 0)
            total_minutes += ticket_minutes
            labour_groups = item.get("labour_groups") or []
            if labour_groups:
                for group in labour_groups:
                    group_minutes = int(group.get("minutes") or 0)
                    if group_minutes <= 0:
                        continue
                    hours_decimal = _minutes_to_hours(group_minutes)
                    description = _format_line_description(
                        line_item_template,
                        ticket,
                        group,
                        group_minutes,
                    )
                    line_item: dict[str, Any] = {
                        "Description": description,
                        "Quantity": float(hours_decimal),
                        "UnitAmount": float(_quantize(rate_decimal)),
                        "AccountCode": str(account_code or "").strip(),
                    }
                    labour_code = str(group.get("code") or "").strip()
                    if labour_code:
                        line_item["ItemCode"] = labour_code
                    if tax_type:
                        line_item["TaxType"] = str(tax_type).strip()
                    line_items.append(line_item)
            else:
                hours_decimal = _minutes_to_hours(ticket_minutes)
                description = _format_line_description(
                    line_item_template,
                    ticket,
                    None,
                    ticket_minutes,
                )
                line_item = {
                    "Description": description,
                    "Quantity": float(hours_decimal),
                    "UnitAmount": float(_quantize(rate_decimal)),
                    "AccountCode": str(account_code or "").strip(),
                }
                if tax_type:
                    line_item["TaxType"] = str(tax_type).strip()
                line_items.append(line_item)
            context_tickets.append(
                {
                    "id": ticket.get("id"),
                    "subject": ticket.get("subject"),
                    "status": ticket.get("status"),
                    "billable_minutes": ticket_minutes,
                    "labour_groups": labour_groups,
                }
            )

        if not line_items:
            continue

        contact_payload: dict[str, Any] = {}
        xero_id = company_record.get("xero_id")
        if xero_id:
            contact_payload["ContactID"] = str(xero_id)
        name = (company_record.get("name") or f"Company #{company_id}").strip()
        if not contact_payload:
            contact_payload["Name"] = name

        invoice_key = (company_id, invoice_day)
        ticket_numbers = _collect_ticket_numbers(context_tickets)
        existing_invoice = invoice_lookup.get(invoice_key)
        if existing_invoice:
            target_invoice = existing_invoice
            target_invoice.setdefault("line_items", []).extend(line_items)
            context = target_invoice.setdefault("context", {})
            existing_tickets = context.setdefault("tickets", [])
            existing_tickets.extend(context_tickets)
            current_minutes = int(context.get("total_billable_minutes") or 0)
            context["total_billable_minutes"] = current_minutes + total_minutes
            context.setdefault(
                "company",
                {
                    "id": company_record.get("id", company_id),
                    "name": name,
                    "xero_id": company_record.get("xero_id"),
                },
            )
            context["invoice_date"] = invoice_day.isoformat()
            merged_ticket_numbers = _collect_ticket_numbers(existing_tickets)
            target_invoice["reference"] = _build_reference(reference_prefix, merged_ticket_numbers)
            if id(target_invoice) not in appended_invoices:
                invoices.append(target_invoice)
                appended_invoices.add(id(target_invoice))
            continue

        invoice = {
            "type": "ACCREC",
            "contact": contact_payload,
            "line_items": line_items,
            "line_amount_type": line_amount_type or "Exclusive",
            "reference": _build_reference(reference_prefix, ticket_numbers),
            "context": {
                "company": {
                    "id": company_record.get("id", company_id),
                    "name": name,
                    "xero_id": company_record.get("xero_id"),
                },
                "tickets": context_tickets,
                "total_billable_minutes": total_minutes,
                "invoice_date": invoice_day.isoformat(),
            },
        }
        invoice_lookup[invoice_key] = invoice
        invoices.append(invoice)
        appended_invoices.add(id(invoice))

    return invoices


async def build_order_invoice(
    order_number: str,
    company_id: int,
    *,
    account_code: str,
    tax_type: str | None,
    line_amount_type: str,
    fetch_summary: OrderSummaryFetcher,
    fetch_items: OrderItemsFetcher,
    fetch_company: CompanyFetcher,
    user_name: str | None = None,
) -> dict[str, Any] | None:
    """Prepare a Xero invoice payload for a shop order.
    
    Args:
        order_number: The order number
        company_id: The company ID
        account_code: Xero account code
        tax_type: Optional tax type
        line_amount_type: Line amount type (Exclusive/Inclusive)
        fetch_summary: Function to fetch order summary
        fetch_items: Function to fetch order items
        fetch_company: Function to fetch company details
        user_name: Optional name of the user who placed the order
        
    Returns:
        Invoice payload dictionary or None if order not found
    """

    summary = await fetch_summary(order_number, company_id)
    items = await fetch_items(order_number, company_id) or []
    if not summary or not items:
        return None

    company_record = await fetch_company(company_id) or {}
    contact_payload: dict[str, Any] = {}
    xero_id = company_record.get("xero_id")
    if xero_id:
        contact_payload["ContactID"] = str(xero_id)
    name = (company_record.get("name") or f"Company #{company_id}").strip()
    if not contact_payload:
        contact_payload["Name"] = name

    line_items: list[dict[str, Any]] = []
    context_items: list[dict[str, Any]] = []
    for item in items:
        quantity_decimal = _to_decimal(item.get("quantity")) or Decimal("0")
        price_decimal = _to_decimal(item.get("price")) or Decimal("0")
        quantity = float(_quantize(quantity_decimal, "0.01"))
        unit_amount = float(_quantize(price_decimal, "0.01"))
        line_item: dict[str, Any] = {
            "Description": str(item.get("product_name") or "Item").strip(),
            "Quantity": quantity,
            "UnitAmount": unit_amount,
            "AccountCode": str(account_code or "").strip(),
        }
        sku = item.get("sku")
        if sku:
            line_item["ItemCode"] = str(sku)
        if tax_type:
            line_item["TaxType"] = str(tax_type).strip()
        line_items.append(line_item)
        context_items.append(
            {
                "quantity": quantity,
                "price": unit_amount,
                "product_name": item.get("product_name"),
                "sku": sku,
            }
        )

    # Add line item with user information and order number
    if user_name:
        user_info_line = {
            "Description": f"Order {order_number} placed by {user_name}",
            "Quantity": 0,
            "UnitAmount": 0,
            "AccountCode": str(account_code or "").strip(),
        }
        if tax_type:
            user_info_line["TaxType"] = str(tax_type).strip()
        line_items.append(user_info_line)

    # Use PO number as reference if available, otherwise use order number
    po_number = summary.get("po_number")
    reference = str(po_number).strip() if po_number else order_number

    invoice = {
        "type": "ACCREC",
        "contact": contact_payload,
        "line_items": line_items,
        "line_amount_type": line_amount_type or "Exclusive",
        "reference": reference,
        "context": {
            "order": summary,
            "items": context_items,
            "company": {
                "id": company_record.get("id", company_id),
                "name": name,
                "xero_id": company_record.get("xero_id"),
            },
            "user_name": user_name,
        },
    }
    return invoice


def _evaluate_qty_expression(expression: str, context: dict[str, Any]) -> float:
    """Evaluate a quantity expression, supporting both static numbers and variables.
    
    Args:
        expression: The quantity expression (e.g., "5", "{active_agents}", "10")
        context: Dictionary of available variables for substitution
    
    Returns:
        The evaluated quantity as a float
    """
    if not expression:
        return 1.0
    
    # Try to evaluate as a direct number first
    try:
        return float(expression)
    except ValueError:
        pass
    
    # Try to substitute variables and then evaluate
    try:
        # Simple variable substitution using format_map
        evaluated = expression.format_map(_TemplateValues(context))
        return float(evaluated)
    except (ValueError, KeyError):
        # If evaluation fails, default to 1
        logger.warning(
            "Failed to evaluate quantity expression, defaulting to 1",
            expression=expression,
            context_keys=list(context.keys()),
        )
        return 1.0


async def build_invoice_context(company_id: int) -> dict[str, Any]:
    """Build context variables for invoice template substitution.
    
    Args:
        company_id: The company ID to build context for
    
    Returns:
        Dictionary of available variables including device counts
    """
    # Calculate date for "last month" - assets synced in the last 30 days
    since_date = datetime.now(timezone.utc) - timedelta(days=30)
    
    # Count total active assets
    total_assets = await assets_repo.count_active_assets(
        company_id=company_id,
        since=since_date,
    )
    
    # Count assets by device type
    workstation_count = await assets_repo.count_active_assets_by_type(
        company_id=company_id,
        since=since_date,
        device_type="Workstation",
    )
    
    server_count = await assets_repo.count_active_assets_by_type(
        company_id=company_id,
        since=since_date,
        device_type="Server",
    )
    
    user_count = await assets_repo.count_active_assets_by_type(
        company_id=company_id,
        since=since_date,
        device_type="User",
    )
    
    # Get company details
    company = await company_repo.get_company_by_id(company_id)
    company_name = company.get("name") if company else f"Company {company_id}"
    
    return {
        "company_id": company_id,
        "company_name": company_name,
        "active_agents": total_assets,
        "active_workstations": workstation_count,
        "active_servers": server_count,
        "active_users": user_count,
        "total_assets": total_assets,
    }


async def build_recurring_invoice_items(
    company_id: int,
    *,
    tax_type: str | None,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build line items for recurring invoice items configured for a company.
    
    Args:
        company_id: The company ID to fetch recurring items for
        tax_type: Tax type to apply to line items
        context: Dictionary of variables available for template substitution
    
    Returns:
        List of Xero line item dictionaries
    """
    recurring_items = await recurring_items_repo.list_company_recurring_invoice_items(company_id)
    if not recurring_items:
        return []
    
    template_context = _TemplateValues(context or {})
    line_items: list[dict[str, Any]] = []
    
    for item in recurring_items:
        # Skip inactive items
        if not item.get("active"):
            continue
        
        # Format description using template
        description_template = str(item.get("description_template") or "")
        try:
            description = description_template.format_map(template_context).strip()
        except Exception:
            description = description_template.strip()
        
        if not description:
            description = str(item.get("product_code") or "Item")
        
        # Evaluate quantity expression
        qty_expression = str(item.get("qty_expression") or "1")
        quantity = _evaluate_qty_expression(qty_expression, template_context)
        
        # Build the line item
        line_item: dict[str, Any] = {
            "Description": description,
            "Quantity": quantity,
            "ItemCode": str(item.get("product_code") or ""),
        }
        
        # Add price override if specified
        price_override = item.get("price_override")
        if price_override is not None:
            try:
                unit_amount = float(price_override)
                line_item["UnitAmount"] = unit_amount
            except (TypeError, ValueError):
                pass
        
        # Add tax type if specified
        if tax_type:
            line_item["TaxType"] = str(tax_type).strip()
        
        line_items.append(line_item)
    
    return line_items


async def sync_billable_tickets(
    company_id: int,
    *,
    billable_statuses: Sequence[str] | str | None = None,
    hourly_rate: Decimal,
    account_code: str,
    tax_type: str | None,
    line_amount_type: str,
    reference_prefix: str,
    description_template: str | None = None,
    tenant_id: str,
    access_token: str,
    auto_send: bool = False,
) -> dict[str, Any]:
    """Sync billable tickets for a company to Xero.
    
    This function:
    1. Finds tickets matching billable statuses that have unbilled time entries
    2. Groups billable time by ticket and labour type
    3. Creates invoice line items
    4. Submits invoice to Xero
    5. Records billed time entries to prevent duplicate billing
    6. Moves billed tickets to "Closed" status
    7. Records invoice number on tickets
    
    Args:
        company_id: The company to sync tickets for
        billable_statuses: List of ticket statuses that are billable
        hourly_rate: Hourly rate for billing
        account_code: Xero account code
        tax_type: Xero tax type
        line_amount_type: Xero line amount type (Exclusive/Inclusive)
        reference_prefix: Prefix for invoice reference
        description_template: Template for line item descriptions
        tenant_id: Xero tenant ID
        access_token: Xero API access token
        auto_send: If True, invoice will be set to AUTHORISED status and sent to contact
        
    Returns:
        Dictionary with sync status and details
    """
    
    # Normalize billable statuses
    status_filter = _normalise_status_filter(billable_statuses)
    if not status_filter:
        return {
            "status": "skipped",
            "reason": "No billable statuses configured",
            "company_id": company_id,
        }
    
    # Find tickets for this company matching billable statuses
    tickets = await tickets_repo.list_tickets(
        company_id=company_id,
        limit=1000,
    )
    
    # Filter to only billable status tickets with unbilled time
    billable_tickets: list[dict[str, Any]] = []
    for ticket in tickets:
        ticket_status = str(ticket.get("status") or "").strip().lower()
        if ticket_status not in status_filter:
            continue
        
        # Check if ticket has any unbilled time entries
        ticket_id = ticket.get("id")
        if not ticket_id:
            continue
            
        unbilled_reply_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
        if unbilled_reply_ids:
            billable_tickets.append(ticket)
    
    if not billable_tickets:
        return {
            "status": "skipped",
            "reason": "No billable tickets with unbilled time",
            "company_id": company_id,
            "billable_statuses": list(status_filter),
        }
    
    # Build invoice data using existing build_ticket_invoices function
    async def fetch_ticket(ticket_id: int):
        for t in billable_tickets:
            if t.get("id") == ticket_id:
                return t
        return await tickets_repo.get_ticket(ticket_id)
    
    async def fetch_replies(ticket_id: int):
        # Only return unbilled replies
        unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
        all_replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
        return [r for r in all_replies if r.get("id") in unbilled_ids]
    
    async def fetch_company(cid: int):
        return await company_repo.get_company_by_id(cid)
    
    ticket_ids = [t["id"] for t in billable_tickets]
    invoices = await build_ticket_invoices(
        ticket_ids,
        hourly_rate=hourly_rate,
        account_code=account_code,
        tax_type=tax_type,
        line_amount_type=line_amount_type,
        reference_prefix=reference_prefix,
        description_template=description_template,
        invoice_date=date.today(),
        fetch_ticket=fetch_ticket,
        fetch_replies=fetch_replies,
        fetch_company=fetch_company,
    )
    
    if not invoices:
        return {
            "status": "skipped",
            "reason": "No invoice line items generated",
            "company_id": company_id,
            "tickets_checked": len(billable_tickets),
        }
    
    # We should only have one invoice per company
    if len(invoices) > 1:
        logger.warning(
            "Multiple invoices generated for single company",
            company_id=company_id,
            invoice_count=len(invoices),
        )
    
    # Take the first (and should be only) invoice
    invoice_data = invoices[0]
    context = invoice_data.get("context", {})
    tickets_context = context.get("tickets", [])
    
    # Build Xero invoice payload
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        return {
            "status": "error",
            "reason": "Company not found",
            "company_id": company_id,
        }
    
    xero_id = company.get("xero_id")
    contact_payload: dict[str, Any] = {}
    if xero_id:
        contact_payload["ContactID"] = str(xero_id)
    else:
        company_name = company.get("name") or f"Company #{company_id}"
        contact_payload["Name"] = str(company_name).strip()
    
    invoice_payload = {
        "Type": "ACCREC",
        "Contact": contact_payload,
        "LineItems": invoice_data["line_items"],
        "LineAmountTypes": line_amount_type,
        "Reference": invoice_data["reference"],
        "Date": date.today().isoformat(),
        "Status": "AUTHORISED" if auto_send else "DRAFT",
    }
    
    if auto_send:
        invoice_payload["SentToContact"] = True
    
    # Make API call to Xero
    api_url = "https://api.xero.com/api.xro/2.0/Invoices"
    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "xero-tenant-id": tenant_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    # Create webhook monitor event with the exact request payload so manual retries
    # resend the same body that was submitted to Xero.
    webhook_payload = {"Invoices": [invoice_payload]}

    try:
        event = await webhook_monitor.create_manual_event(
            name="xero.sync.billable_tickets",
            target_url=api_url,
            payload=webhook_payload,
            headers=request_headers,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:
        logger.error("Failed to create webhook monitor event", error=str(exc))
        event = None
    
    event_id: int | None = None
    if event and event.get("id") is not None:
        try:
            event_id = int(event["id"])
        except (TypeError, ValueError):
            event_id = None
    
    # Make HTTP request to Xero
    response_status: int | None = None
    response_body: str | None = None
    response_headers: dict[str, Any] | None = None
    xero_invoice_number: str | None = None
    
    xero_request_payload = {"Invoices": [invoice_payload]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                api_url,
                json=xero_request_payload,
                headers=request_headers,
            )
            response_status = response.status_code
            response_body = response.text
            response_headers = dict(response.headers)
        
        success = 200 <= response_status < 300
        
        # Parse invoice number from response
        if success and response_body:
            try:
                response_data = json.loads(response_body)
                invoices_list = response_data.get("Invoices", [])
                if invoices_list:
                    xero_invoice_number = invoices_list[0].get("InvoiceNumber")
            except Exception as parse_exc:
                logger.warning(
                    "Failed to parse Xero invoice number from response",
                    error=str(parse_exc),
                )
        
        if event_id is not None:
            if success:
                try:
                    await webhook_monitor.record_manual_success(
                        event_id,
                        attempt_number=1,
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook success",
                        event_id=event_id,
                        error=str(record_exc),
                    )
            else:
                try:
                    await webhook_monitor.record_manual_failure(
                        event_id,
                        attempt_number=1,
                        status="failed",
                        error_message=f"HTTP {response_status}",
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook failure",
                        event_id=event_id,
                        error=str(record_exc),
                    )
        
        if success and xero_invoice_number:
            # Record billed time entries
            billed_count = 0
            now = datetime.now(timezone.utc)
            
            for ticket_ctx in tickets_context:
                ticket_id = ticket_ctx.get("id")
                if not ticket_id:
                    continue
                
                # Get all replies for this ticket that were in the invoice
                unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
                replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
                
                for reply in replies:
                    reply_id = reply.get("id")
                    if reply_id not in unbilled_ids:
                        continue
                    
                    # Ensure entry is billable
                    is_billable = reply.get("is_billable")
                    if not is_billable:
                        continue
                    
                    minutes = reply.get("minutes_spent")
                    if not minutes or minutes <= 0:
                        continue
                    
                    labour_type_id = reply.get("labour_type_id")
                    
                    try:
                        await billed_time_repo.create_billed_time_entry(
                            ticket_id=ticket_id,
                            reply_id=reply_id,
                            xero_invoice_number=xero_invoice_number,
                            minutes_billed=minutes,
                            labour_type_id=labour_type_id,
                        )
                        billed_count += 1
                    except Exception as entry_exc:
                        logger.error(
                            "Failed to record billed time entry",
                            ticket_id=ticket_id,
                            reply_id=reply_id,
                            error=str(entry_exc),
                        )
                
                # Update ticket: mark as billed and move to Closed status
                try:
                    await tickets_repo.update_ticket(
                        ticket_id,
                        xero_invoice_number=xero_invoice_number,
                        billed_at=now,
                        status="closed",
                        closed_at=now,
                    )
                except Exception as update_exc:
                    logger.error(
                        "Failed to update ticket billing status",
                        ticket_id=ticket_id,
                        error=str(update_exc),
                    )
            
            logger.info(
                "Successfully synced billable tickets to Xero",
                company_id=company_id,
                invoice_number=xero_invoice_number,
                tickets_billed=len(tickets_context),
                time_entries_recorded=billed_count,
                response_status=response_status,
                event_id=event_id,
            )
            
            return {
                "status": "succeeded",
                "company_id": company_id,
                "invoice_number": xero_invoice_number,
                "tickets_billed": len(tickets_context),
                "time_entries_recorded": billed_count,
                "response_status": response_status,
                "event_id": event_id,
            }
        else:
            logger.error(
                "Xero API returned error status for tickets",
                company_id=company_id,
                response_status=response_status,
                response_body=response_body,
            )
            return {
                "status": "failed",
                "company_id": company_id,
                "response_status": response_status,
                "error": f"HTTP {response_status}",
                "event_id": event_id,
            }
    
    except httpx.HTTPError as exc:
        logger.error("Xero API request failed for tickets", company_id=company_id, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=response_status,
                    response_body=response_body,
                    request_headers=request_headers,
                    request_body=invoice_payload,
                    response_headers=response_headers,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "company_id": company_id,
            "error": str(exc),
            "event_id": event_id,
        }
    except Exception as exc:
        logger.error("Unexpected error during Xero tickets sync", company_id=company_id, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=None,
                    response_body=None,
                    request_headers=request_headers,
                    request_body=invoice_payload,
                    response_headers=None,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "company_id": company_id,
            "error": str(exc),
            "event_id": event_id,
        }


async def sync_company(company_id: int, auto_send: bool = False) -> dict[str, Any]:
    """Trigger a Xero synchronisation for the given company.

    This implementation makes HTTP calls to Xero API and records them
    in the webhook monitor for tracking and debugging.
    
    Args:
        company_id: The company to sync
        auto_send: If True, invoice will be set to AUTHORISED status and sent to contact
    """

    module = await modules_service.get_module("xero", redact=False)
    if not module or not module.get("enabled"):
        return {
            "status": "skipped",
            "reason": "Module disabled",
            "company_id": company_id,
        }

    settings = dict(module.get("settings") or {})
    required_fields = ["client_id", "client_secret", "refresh_token", "tenant_id"]
    missing = [field for field in required_fields if not str(settings.get(field) or "").strip()]
    if missing:
        return {
            "status": "skipped",
            "reason": "Module not fully configured",
            "missing": missing,
            "company_id": company_id,
        }

    company = await company_repo.get_company_by_id(company_id)
    if not company:
        return {
            "status": "skipped",
            "reason": "Company not found",
            "company_id": company_id,
        }

    # Get tenant_id for API calls
    tenant_id = str(settings.get("tenant_id", "")).strip()
    if not tenant_id:
        return {
            "status": "skipped",
            "reason": "Tenant ID not configured",
            "company_id": company_id,
        }

    # Fetch recurring invoice items for this company
    recurring_items = await recurring_items_repo.list_company_recurring_invoice_items(company_id)
    recurring_items_info = []
    for item in recurring_items:
        if item.get("active"):
            recurring_items_info.append({
                "product_code": item.get("product_code"),
                "description_template": item.get("description_template"),
                "qty_expression": item.get("qty_expression"),
                "price_override": item.get("price_override"),
            })

    # Build context for invoice line item template substitution
    context = await build_invoice_context(company_id)
    
    # Build line items from recurring invoice items
    tax_type = str(settings.get("tax_type", "")).strip() or None
    line_items = await build_recurring_invoice_items(
        company_id,
        tax_type=tax_type,
        context=context,
    )

    # Get or refresh access token (needed for both tickets and recurring items)
    try:
        access_token = await modules_service.acquire_xero_access_token()
    except Exception as exc:
        logger.error("Failed to acquire Xero access token", error=str(exc))
        return {
            "status": "error",
            "reason": "Failed to acquire access token",
            "error": str(exc),
            "company_id": company_id,
        }
    
    # Build ticket line items if billable tickets are configured
    billable_statuses_raw = settings.get("billable_statuses")
    ticket_line_items: list[dict[str, Any]] = []
    tickets_context: list[dict[str, Any]] = []
    ticket_numbers: list[str] = []
    
    if billable_statuses_raw:
        try:
            hourly_rate_str = str(settings.get("default_hourly_rate", "")).strip()
            hourly_rate = Decimal(hourly_rate_str) if hourly_rate_str else Decimal("0")
        except (InvalidOperation, ValueError):
            hourly_rate = Decimal("0")
        
        if hourly_rate > 0:
            account_code = str(settings.get("account_code", "")).strip() or "400"
            description_template = str(settings.get("line_item_description_template", "")).strip()
            
            # Normalize billable statuses
            status_filter = _normalise_status_filter(billable_statuses_raw)
            if status_filter:
                # Find tickets for this company matching billable statuses
                tickets = await tickets_repo.list_tickets(
                    company_id=company_id,
                    limit=1000,
                )
                
                # Filter to only billable status tickets with unbilled time
                billable_tickets: list[dict[str, Any]] = []
                for ticket in tickets:
                    ticket_status = str(ticket.get("status") or "").strip().lower()
                    if ticket_status not in status_filter:
                        continue
                    
                    # Check if ticket has any unbilled time entries
                    ticket_id = ticket.get("id")
                    if not ticket_id:
                        continue
                        
                    unbilled_reply_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
                    if unbilled_reply_ids:
                        billable_tickets.append(ticket)
                
                if billable_tickets:
                    # Build line items for billable tickets
                    for ticket in billable_tickets:
                        ticket_id = ticket.get("id")
                        if not ticket_id:
                            continue
                        
                        # Get unbilled replies for this ticket
                        unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
                        all_replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
                        unbilled_replies = [r for r in all_replies if r.get("id") in unbilled_ids]
                        
                        # Group by labour type
                        labour_map: dict[tuple[str | None, str | None], dict[str, Any]] = {}
                        billable_minutes = 0
                        
                        for reply in unbilled_replies:
                            minutes = _coerce_minutes(reply.get("minutes_spent"))
                            if minutes <= 0:
                                continue
                            is_billable = reply.get("is_billable")
                            if not is_billable:
                                continue
                            billable_minutes += minutes
                            labour_code = str(reply.get("labour_type_code") or "").strip() or None
                            labour_name = str(reply.get("labour_type_name") or "").strip() or None
                            key = (labour_code, labour_name)
                            bucket = labour_map.get(key)
                            if not bucket:
                                bucket = {"minutes": 0, "code": labour_code, "name": labour_name}
                                labour_map[key] = bucket
                            bucket["minutes"] += minutes
                        
                        if billable_minutes <= 0:
                            continue
                        
                        # Create line items for this ticket
                        labour_groups = list(labour_map.values())
                        if labour_groups:
                            for group in labour_groups:
                                group_minutes = int(group.get("minutes") or 0)
                                if group_minutes <= 0:
                                    continue
                                hours_decimal = _minutes_to_hours(group_minutes)
                                description = _format_line_description(
                                    description_template,
                                    ticket,
                                    group,
                                    group_minutes,
                                )
                                line_item: dict[str, Any] = {
                                    "Description": description,
                                    "Quantity": float(hours_decimal),
                                    "UnitAmount": float(_quantize(hourly_rate)),
                                    "AccountCode": str(account_code or "").strip(),
                                }
                                labour_code = str(group.get("code") or "").strip()
                                if labour_code:
                                    line_item["ItemCode"] = labour_code
                                if tax_type:
                                    line_item["TaxType"] = str(tax_type).strip()
                                ticket_line_items.append(line_item)
                        else:
                            hours_decimal = _minutes_to_hours(billable_minutes)
                            description = _format_line_description(
                                description_template,
                                ticket,
                                None,
                                billable_minutes,
                            )
                            line_item = {
                                "Description": description,
                                "Quantity": float(hours_decimal),
                                "UnitAmount": float(_quantize(hourly_rate)),
                                "AccountCode": str(account_code or "").strip(),
                            }
                            if tax_type:
                                line_item["TaxType"] = str(tax_type).strip()
                            ticket_line_items.append(line_item)
                        
                        # Track ticket context for post-processing
                        tickets_context.append({
                            "id": ticket_id,
                            "subject": ticket.get("subject"),
                            "status": ticket.get("status"),
                            "billable_minutes": billable_minutes,
                            "labour_groups": labour_groups,
                        })
                        
                        # Collect ticket numbers for reference
                        ticket_num = str(ticket_id)
                        if ticket_num not in ticket_numbers:
                            ticket_numbers.append(ticket_num)

    # Combine recurring and ticket line items
    combined_line_items = line_items + ticket_line_items
    
    # If no line items at all, skip
    if not combined_line_items:
        logger.info(
            "No active recurring invoice items or billable tickets for company",
            company_id=company_id,
        )
        return {
            "status": "skipped",
            "reason": "No active recurring invoice items or billable tickets",
            "company_id": company_id,
            "recurring_items_count": len(recurring_items_info),
        }

    # Build invoice payload for Xero API
    xero_id = company.get("xero_id")
    contact_payload: dict[str, Any] = {}
    if xero_id:
        contact_payload["ContactID"] = str(xero_id)
    else:
        company_name = company.get("name") or f"Company #{company_id}"
        contact_payload["Name"] = str(company_name).strip()

    line_amount_type = str(settings.get("line_amount_type", "")).strip() or "Exclusive"
    reference_prefix = str(settings.get("reference_prefix", "")).strip() or "Support"
    
    # Build reference including ticket numbers if any
    reference = _build_reference(reference_prefix, ticket_numbers)
    
    invoice_payload = {
        "Type": "ACCREC",
        "Contact": contact_payload,
        "LineItems": combined_line_items,
        "LineAmountTypes": line_amount_type,
        "Reference": reference,
        "Date": date.today().isoformat(),
        "Status": "AUTHORISED" if auto_send else "DRAFT",
    }
    
    if auto_send:
        invoice_payload["SentToContact"] = True

    # Prepare for API call
    api_url = f"https://api.xero.com/api.xro/2.0/Invoices"

    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "xero-tenant-id": tenant_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Store the exact request payload so that manual retries resubmit a valid body.
    webhook_payload = {"Invoices": [invoice_payload]}

    try:
        event = await webhook_monitor.create_manual_event(
            name="xero.sync.company",
            target_url=api_url,
            payload=webhook_payload,
            headers=request_headers,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:
        logger.error("Failed to create webhook monitor event", error=str(exc))
        event = None

    event_id: int | None = None
    if event and event.get("id") is not None:
        try:
            event_id = int(event["id"])
        except (TypeError, ValueError):
            event_id = None

    # Make the HTTP request to Xero
    response_status: int | None = None
    response_body: str | None = None
    response_headers: dict[str, Any] | None = None
    
    xero_request_payload = {"Invoices": [invoice_payload]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                api_url,
                json=xero_request_payload,
                headers=request_headers,
            )
            response_status = response.status_code
            response_body = response.text
            response_headers = dict(response.headers)
        
        # Check if request was successful
        success = 200 <= response_status < 300
        
        if event_id is not None:
            if success:
                try:
                    await webhook_monitor.record_manual_success(
                        event_id,
                        attempt_number=1,
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook success",
                        event_id=event_id,
                        error=str(record_exc),
                    )
            else:
                try:
                    await webhook_monitor.record_manual_failure(
                        event_id,
                        attempt_number=1,
                        status="failed",
                        error_message=f"HTTP {response_status}",
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook failure",
                        event_id=event_id,
                        error=str(record_exc),
                    )
        
        if success:
            # Parse invoice number from response
            xero_invoice_number: str | None = None
            if response_body:
                try:
                    response_data = json.loads(response_body)
                    invoices_list = response_data.get("Invoices", [])
                    if invoices_list:
                        xero_invoice_number = invoices_list[0].get("InvoiceNumber")
                except Exception as parse_exc:
                    logger.warning(
                        "Failed to parse Xero invoice number from response",
                        error=str(parse_exc),
                    )
            
            # Record billed time entries and update ticket statuses if we billed tickets
            billed_count = 0
            if tickets_context and xero_invoice_number:
                now = datetime.now(timezone.utc)
                
                for ticket_ctx in tickets_context:
                    ticket_id = ticket_ctx.get("id")
                    if not ticket_id:
                        continue
                    
                    # Get all replies for this ticket that were in the invoice
                    unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(ticket_id)
                    replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
                    
                    for reply in replies:
                        reply_id = reply.get("id")
                        if reply_id not in unbilled_ids:
                            continue
                        
                        # Ensure entry is billable
                        is_billable = reply.get("is_billable")
                        if not is_billable:
                            continue
                        
                        minutes = reply.get("minutes_spent")
                        if not minutes or minutes <= 0:
                            continue
                        
                        labour_type_id = reply.get("labour_type_id")
                        
                        try:
                            await billed_time_repo.create_billed_time_entry(
                                ticket_id=ticket_id,
                                reply_id=reply_id,
                                xero_invoice_number=xero_invoice_number,
                                minutes_billed=minutes,
                                labour_type_id=labour_type_id,
                            )
                            billed_count += 1
                        except Exception as entry_exc:
                            logger.error(
                                "Failed to record billed time entry",
                                ticket_id=ticket_id,
                                reply_id=reply_id,
                                error=str(entry_exc),
                            )
                    
                    # Update ticket: mark as billed and move to Closed status
                    try:
                        await tickets_repo.update_ticket(
                            ticket_id,
                            xero_invoice_number=xero_invoice_number,
                            billed_at=now,
                            status="closed",
                            closed_at=now,
                        )
                    except Exception as update_exc:
                        logger.error(
                            "Failed to update ticket billing status",
                            ticket_id=ticket_id,
                            error=str(update_exc),
                        )
            
            logger.info(
                "Successfully synced company to Xero",
                company_id=company_id,
                tenant_id=tenant_id,
                response_status=response_status,
                event_id=event_id,
                invoice_number=xero_invoice_number,
                tickets_billed=len(tickets_context),
                time_entries_recorded=billed_count,
            )
            return {
                "status": "succeeded",
                "company_id": company_id,
                "tenant_id": tenant_id,
                "response_status": response_status,
                "event_id": event_id,
                "recurring_items_count": len(recurring_items_info),
                "invoice_number": xero_invoice_number,
                "tickets_billed": len(tickets_context),
                "time_entries_recorded": billed_count,
            }
        else:
            logger.error(
                "Xero API returned error status",
                company_id=company_id,
                response_status=response_status,
                response_body=response_body,
            )
            return {
                "status": "failed",
                "company_id": company_id,
                "response_status": response_status,
                "error": f"HTTP {response_status}",
                "event_id": event_id,
            }
    
    except httpx.HTTPError as exc:
        logger.error("Xero API request failed", company_id=company_id, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=response_status,
                    response_body=response_body,
                    request_headers=request_headers,
                    request_body=invoice_payload,
                    response_headers=response_headers,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "company_id": company_id,
            "error": str(exc),
            "event_id": event_id,
        }
    except Exception as exc:
        logger.error("Unexpected error during Xero sync", company_id=company_id, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=None,
                    response_body=None,
                    request_headers=request_headers,
                    request_body=invoice_payload,
                    response_headers=None,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "company_id": company_id,
            "error": str(exc),
            "event_id": event_id,
        }



async def send_order_to_xero(
    order_number: str,
    company_id: int,
    user_name: str | None = None,
) -> dict[str, Any]:
    """Send a shop order to Xero for invoicing.
    
    Args:
        order_number: The order number to invoice
        company_id: The company ID
        user_name: Optional name of the user who placed the order
        
    Returns:
        Dictionary with status and details of the operation
    """
    
    # Check if Xero module is enabled and configured
    module = await modules_service.get_module("xero", redact=False)
    if not module or not module.get("enabled"):
        return {
            "status": "skipped",
            "reason": "Xero module is disabled",
            "order_number": order_number,
            "company_id": company_id,
        }
    
    settings = dict(module.get("settings") or {})
    required_fields = ["client_id", "client_secret", "refresh_token", "tenant_id"]
    missing = [field for field in required_fields if not str(settings.get(field) or "").strip()]
    if missing:
        return {
            "status": "skipped",
            "reason": "Xero module not fully configured",
            "missing": missing,
            "order_number": order_number,
            "company_id": company_id,
        }
    
    # Get settings for invoice
    account_code = str(settings.get("account_code", "")).strip() or "200"
    tax_type = str(settings.get("tax_type", "")).strip() or None
    line_amount_type = str(settings.get("line_amount_type", "")).strip() or "Exclusive"
    tenant_id = str(settings.get("tenant_id", "")).strip()
    
    # Get access token
    try:
        access_token = await modules_service.acquire_xero_access_token()
    except Exception as exc:
        logger.error("Failed to acquire Xero access token for order", error=str(exc))
        return {
            "status": "error",
            "reason": "Failed to acquire access token",
            "error": str(exc),
            "order_number": order_number,
            "company_id": company_id,
        }
    
    # Build invoice payload
    from app.repositories import shop as shop_repo
    
    invoice_data = await build_order_invoice(
        order_number=order_number,
        company_id=company_id,
        account_code=account_code,
        tax_type=tax_type,
        line_amount_type=line_amount_type,
        fetch_summary=shop_repo.get_order_summary,
        fetch_items=shop_repo.list_order_items,
        fetch_company=company_repo.get_company_by_id,
        user_name=user_name,
    )
    
    if not invoice_data:
        return {
            "status": "skipped",
            "reason": "Order not found or has no items",
            "order_number": order_number,
            "company_id": company_id,
        }
    
    # Prepare Xero API payload
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        return {
            "status": "error",
            "reason": "Company not found",
            "order_number": order_number,
            "company_id": company_id,
        }
    
    xero_payload = {
        "Type": "ACCREC",
        "Contact": invoice_data["contact"],
        "LineItems": invoice_data["line_items"],
        "LineAmountTypes": invoice_data["line_amount_type"],
        "Reference": invoice_data["reference"],
        "Date": date.today().isoformat(),
        "Status": "DRAFT",
    }
    
    # Make API call to Xero
    api_url = "https://api.xero.com/api.xro/2.0/Invoices"
    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "xero-tenant-id": tenant_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    # Persist the exact request payload for webhook retries
    webhook_payload = {"Invoices": [xero_payload]}

    try:
        event = await webhook_monitor.create_manual_event(
            name="xero.order.created",
            target_url=api_url,
            payload=webhook_payload,
            headers=request_headers,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:
        logger.error("Failed to create webhook monitor event for order", error=str(exc))
        event = None
    
    event_id: int | None = None
    if event and event.get("id") is not None:
        try:
            event_id = int(event["id"])
        except (TypeError, ValueError):
            event_id = None
    
    # Make HTTP request to Xero
    response_status: int | None = None
    response_body: str | None = None
    response_headers: dict[str, Any] | None = None
    xero_invoice_number: str | None = None
    
    xero_request_payload = {"Invoices": [xero_payload]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                api_url,
                json=xero_request_payload,
                headers=request_headers,
            )
            response_status = response.status_code
            response_body = response.text
            response_headers = dict(response.headers)
        
        success = 200 <= response_status < 300
        
        # Parse invoice number from response
        if success and response_body:
            try:
                response_data = json.loads(response_body)
                invoices_list = response_data.get("Invoices", [])
                if invoices_list:
                    xero_invoice_number = invoices_list[0].get("InvoiceNumber")
            except Exception as parse_exc:
                logger.warning(
                    "Failed to parse Xero invoice number from order response",
                    error=str(parse_exc),
                )
        
        if event_id is not None:
            if success:
                try:
                    await webhook_monitor.record_manual_success(
                        event_id,
                        attempt_number=1,
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook success for order",
                        event_id=event_id,
                        error=str(record_exc),
                    )
            else:
                try:
                    await webhook_monitor.record_manual_failure(
                        event_id,
                        attempt_number=1,
                        status="failed",
                        error_message=f"HTTP {response_status}",
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook failure for order",
                        event_id=event_id,
                        error=str(record_exc),
                    )
        
        if success:
            logger.info(
                "Successfully sent order to Xero",
                order_number=order_number,
                company_id=company_id,
                invoice_number=xero_invoice_number,
                response_status=response_status,
                event_id=event_id,
            )
            return {
                "status": "succeeded",
                "order_number": order_number,
                "company_id": company_id,
                "invoice_number": xero_invoice_number,
                "response_status": response_status,
                "event_id": event_id,
            }
        else:
            logger.error(
                "Xero API returned error status for order",
                order_number=order_number,
                company_id=company_id,
                response_status=response_status,
                response_body=response_body,
            )
            return {
                "status": "failed",
                "order_number": order_number,
                "company_id": company_id,
                "response_status": response_status,
                "error": f"HTTP {response_status}",
                "event_id": event_id,
            }
    
    except httpx.HTTPError as exc:
        logger.error("Xero API request failed for order", order_number=order_number, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=response_status,
                    response_body=response_body,
                    request_headers=request_headers,
                    request_body=xero_request_payload,
                    response_headers=response_headers,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error for order",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "order_number": order_number,
            "company_id": company_id,
            "error": str(exc),
            "event_id": event_id,
        }
    except Exception as exc:
        logger.error("Unexpected error sending order to Xero", order_number=order_number, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=None,
                    response_body=None,
                    request_headers=request_headers,
                    request_body=xero_request_payload,
                    response_headers=None,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error for order",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "order_number": order_number,
            "company_id": company_id,
            "error": str(exc),
            "event_id": event_id,
        }


async def send_subscription_charge_to_xero(
    subscription_id: str,
    change_request_id: str,
    customer_id: int,
    product_name: str,
    quantity_change: int,
    prorated_charge: Decimal,
    end_date: date,
) -> dict[str, Any]:
    """Send a subscription charge to Xero for invoicing.
    
    Args:
        subscription_id: The subscription ID
        change_request_id: The change request ID
        customer_id: The customer/company ID
        product_name: The product/subscription name
        quantity_change: Number of licenses added
        prorated_charge: The prorated charge amount
        end_date: The subscription end date
        
    Returns:
        Dictionary with status and details of the operation
    """
    
    # Check if Xero module is enabled and configured
    module = await modules_service.get_module("xero", redact=False)
    if not module or not module.get("enabled"):
        return {
            "status": "skipped",
            "reason": "Xero module is disabled",
            "subscription_id": subscription_id,
            "customer_id": customer_id,
        }
    
    settings = dict(module.get("settings") or {})
    required_fields = ["client_id", "client_secret", "refresh_token", "tenant_id"]
    missing = [field for field in required_fields if not str(settings.get(field) or "").strip()]
    if missing:
        return {
            "status": "skipped",
            "reason": "Xero module not fully configured",
            "missing": missing,
            "subscription_id": subscription_id,
            "customer_id": customer_id,
        }
    
    # Get settings for invoice
    account_code = str(settings.get("account_code", "")).strip() or "200"
    tax_type = str(settings.get("tax_type", "")).strip() or None
    line_amount_type = str(settings.get("line_amount_type", "")).strip() or "Exclusive"
    tenant_id = str(settings.get("tenant_id", "")).strip()
    reference_prefix = str(settings.get("reference_prefix", "")).strip() or "Support"
    
    # Get access token
    try:
        access_token = await modules_service.acquire_xero_access_token()
    except Exception as exc:
        logger.error("Failed to acquire Xero access token for subscription charge", error=str(exc))
        return {
            "status": "error",
            "reason": "Failed to acquire access token",
            "error": str(exc),
            "subscription_id": subscription_id,
            "customer_id": customer_id,
        }
    
    # Get company details
    company = await company_repo.get_company_by_id(customer_id)
    if not company:
        return {
            "status": "error",
            "reason": "Company not found",
            "subscription_id": subscription_id,
            "customer_id": customer_id,
        }
    
    # Build invoice line item
    charge_decimal = _to_decimal(prorated_charge) or Decimal("0")
    line_items: list[dict[str, Any]] = []
    
    description = f"Subscription: {product_name} - {quantity_change} license(s) added (prorated to {end_date.isoformat()})"
    
    line_item: dict[str, Any] = {
        "Description": description,
        "Quantity": 1,
        "UnitAmount": float(_quantize(charge_decimal)),
        "AccountCode": str(account_code or "").strip(),
    }
    
    if tax_type:
        line_item["TaxType"] = str(tax_type).strip()
    
    line_items.append(line_item)
    
    # Build contact payload
    xero_id = company.get("xero_id")
    contact_payload: dict[str, Any] = {}
    if xero_id:
        contact_payload["ContactID"] = str(xero_id)
    else:
        company_name = company.get("name") or f"Company #{customer_id}"
        contact_payload["Name"] = str(company_name).strip()
    
    # Build reference
    reference = f"{reference_prefix} - Subscription {subscription_id[:8]}"
    
    # Build Xero invoice payload
    xero_payload = {
        "Type": "ACCREC",
        "Contact": contact_payload,
        "LineItems": line_items,
        "LineAmountTypes": line_amount_type,
        "Reference": reference,
        "Date": date.today().isoformat(),
        "Status": "DRAFT",
    }
    
    # Make API call to Xero
    api_url = "https://api.xero.com/api.xro/2.0/Invoices"
    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "xero-tenant-id": tenant_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    # Create webhook monitor event
    webhook_payload = {
        "subscription_id": subscription_id,
        "change_request_id": change_request_id,
        "customer_id": customer_id,
        "company_name": company.get("name"),
        "product_name": product_name,
        "quantity_change": quantity_change,
        "prorated_charge": float(charge_decimal),
        "invoice": xero_payload,
    }
    
    try:
        event = await webhook_monitor.create_manual_event(
            name="xero.subscription.charge",
            target_url=api_url,
            payload=webhook_payload,
            headers=request_headers,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:
        logger.error("Failed to create webhook monitor event for subscription charge", error=str(exc))
        event = None
    
    event_id: int | None = None
    if event and event.get("id") is not None:
        try:
            event_id = int(event["id"])
        except (TypeError, ValueError):
            event_id = None
    
    # Make HTTP request to Xero
    response_status: int | None = None
    response_body: str | None = None
    response_headers: dict[str, Any] | None = None
    xero_invoice_number: str | None = None
    
    xero_request_payload = {"Invoices": [xero_payload]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                api_url,
                json=xero_request_payload,
                headers=request_headers,
            )
            response_status = response.status_code
            response_body = response.text
            response_headers = dict(response.headers)
        
        success = 200 <= response_status < 300
        
        # Parse invoice number from response
        if success and response_body:
            try:
                response_data = json.loads(response_body)
                invoices_list = response_data.get("Invoices", [])
                if invoices_list:
                    xero_invoice_number = invoices_list[0].get("InvoiceNumber")
            except Exception as parse_exc:
                logger.warning(
                    "Failed to parse Xero invoice number from subscription charge response",
                    error=str(parse_exc),
                )
        
        if event_id is not None:
            if success:
                try:
                    await webhook_monitor.record_manual_success(
                        event_id,
                        attempt_number=1,
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook success for subscription charge",
                        event_id=event_id,
                        error=str(record_exc),
                    )
            else:
                try:
                    await webhook_monitor.record_manual_failure(
                        event_id,
                        attempt_number=1,
                        status="failed",
                        error_message=f"HTTP {response_status}",
                        response_status=response_status,
                        response_body=response_body,
                        request_headers=request_headers,
                        request_body=xero_request_payload,
                        response_headers=response_headers,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Failed to record webhook failure for subscription charge",
                        event_id=event_id,
                        error=str(record_exc),
                    )
        
        if success:
            logger.info(
                "Successfully sent subscription charge to Xero",
                subscription_id=subscription_id,
                customer_id=customer_id,
                invoice_number=xero_invoice_number,
                response_status=response_status,
                event_id=event_id,
            )
            return {
                "status": "succeeded",
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "invoice_number": xero_invoice_number,
                "response_status": response_status,
                "event_id": event_id,
            }
        else:
            logger.error(
                "Xero API returned error status for subscription charge",
                subscription_id=subscription_id,
                customer_id=customer_id,
                response_status=response_status,
                response_body=response_body,
            )
            return {
                "status": "failed",
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "response_status": response_status,
                "error": f"HTTP {response_status}",
                "event_id": event_id,
            }
    
    except httpx.HTTPError as exc:
        logger.error("Xero API request failed for subscription charge", subscription_id=subscription_id, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=response_status,
                    response_body=response_body,
                    request_headers=request_headers,
                    request_body=xero_payload,
                    response_headers=response_headers,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error for subscription charge",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "error": str(exc),
            "event_id": event_id,
        }
    except Exception as exc:
        logger.error("Unexpected error sending subscription charge to Xero", subscription_id=subscription_id, error=str(exc))
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=None,
                    response_body=None,
                    request_headers=request_headers,
                    request_body=xero_payload,
                    response_headers=None,
                )
            except Exception as record_exc:
                logger.error(
                    "Failed to record webhook error for subscription charge",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return {
            "status": "error",
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "error": str(exc),
            "event_id": event_id,
        }
