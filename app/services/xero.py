from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Mapping, MutableMapping, Sequence

from loguru import logger

from app.repositories import companies as company_repo
from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.services import modules as modules_service

TicketFetcher = Callable[[int], Awaitable[Mapping[str, Any] | None]]
RepliesFetcher = Callable[[int], Awaitable[Sequence[Mapping[str, Any]] | None]]
CompanyFetcher = Callable[[int], Awaitable[Mapping[str, Any] | None]]
OrderSummaryFetcher = Callable[[str, int], Awaitable[Mapping[str, Any] | None]]
OrderItemsFetcher = Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]] | None]]


class _TemplateValues(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _normalise_status_filter(statuses: Sequence[Any] | None) -> set[str] | None:
    if not statuses:
        return None
    filtered: set[str] = set()
    for status in statuses:
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
) -> dict[str, Any] | None:
    """Prepare a Xero invoice payload for a shop order."""

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

    invoice = {
        "type": "ACCREC",
        "contact": contact_payload,
        "line_items": line_items,
        "line_amount_type": line_amount_type or "Exclusive",
        "reference": order_number,
        "context": {
            "order": summary,
            "items": context_items,
            "company": {
                "id": company_record.get("id", company_id),
                "name": name,
                "xero_id": company_record.get("xero_id"),
            },
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


async def sync_company(company_id: int) -> dict[str, Any]:
    """Trigger a Xero synchronisation for the given company.

    The implementation focuses on returning structured metadata so that the
    scheduler run history captures useful diagnostics without leaking
    credentials.
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

    payload = {
        "status": "queued",
        "company_id": company_id,
        "module": "xero",
        "tenant_id": settings.get("tenant_id"),
        "account_code": settings.get("account_code"),
        "line_amount_type": settings.get("line_amount_type"),
        "reference_prefix": settings.get("reference_prefix"),
        "billable_statuses": settings.get("billable_statuses"),
        "line_item_description_template": settings.get("line_item_description_template"),
        "recurring_invoice_items": recurring_items_info,
        "company": {
            "id": company.get("id"),
            "name": company.get("name"),
            "xero_id": company.get("xero_id"),
        },
    }
    logger.info(
        "Queued company for Xero synchronisation",
        company_id=company_id,
        tenant_id=settings.get("tenant_id"),
        recurring_items_count=len(recurring_items_info),
    )
    return payload

