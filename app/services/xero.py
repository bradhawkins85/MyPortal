from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Mapping, Sequence

from loguru import logger

from app.repositories import companies as company_repo
from app.services import modules as modules_service

TicketFetcher = Callable[[int], Awaitable[Mapping[str, Any] | None]]
RepliesFetcher = Callable[[int], Awaitable[Sequence[Mapping[str, Any]] | None]]
CompanyFetcher = Callable[[int], Awaitable[Mapping[str, Any] | None]]
OrderSummaryFetcher = Callable[[str, int], Awaitable[Mapping[str, Any] | None]]
OrderItemsFetcher = Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]] | None]]


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
    fetch_ticket: TicketFetcher,
    fetch_replies: RepliesFetcher,
    fetch_company: CompanyFetcher,
) -> list[dict[str, Any]]:
    """Construct invoice payloads for the provided ticket identifiers."""

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
                    description = f"Ticket {ticket.get('id')}: {ticket.get('subject', '').strip()}"
                    labour_name = str(group.get("name") or "").strip()
                    if labour_name:
                        description = f"{description} · {labour_name}"
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
                description = f"Ticket {ticket.get('id')}: {ticket.get('subject', '').strip()}"
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
                    "billable_minutes": ticket_minutes,
                    "labour_groups": labour_groups,
                }
            )

        contact_payload: dict[str, Any] = {}
        xero_id = company_record.get("xero_id")
        if xero_id:
            contact_payload["ContactID"] = str(xero_id)
        name = (company_record.get("name") or f"Company #{company_id}").strip()
        if not contact_payload:
            contact_payload["Name"] = name

        ticket_numbers = ", ".join(str(ticket.get("id")) for ticket in context_tickets if ticket.get("id"))
        reference_parts = [reference_prefix.strip()] if reference_prefix else []
        if ticket_numbers:
            reference_parts.append(f"Tickets {ticket_numbers}")
        reference = " — ".join(part for part in reference_parts if part)

        invoice = {
            "type": "ACCREC",
            "contact": contact_payload,
            "line_items": line_items,
            "line_amount_type": line_amount_type or "Exclusive",
            "reference": reference,
            "context": {
                "company": {
                    "id": company_record.get("id", company_id),
                    "name": name,
                    "xero_id": company_record.get("xero_id"),
                },
                "tickets": context_tickets,
                "total_billable_minutes": total_minutes,
            },
        }
        invoices.append(invoice)

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

    payload = {
        "status": "queued",
        "company_id": company_id,
        "module": "xero",
        "tenant_id": settings.get("tenant_id"),
        "account_code": settings.get("account_code"),
        "line_amount_type": settings.get("line_amount_type"),
        "reference_prefix": settings.get("reference_prefix"),
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
    )
    return payload

