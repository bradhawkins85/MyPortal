"""Portal invoice routes for the ``invoices`` feature pack."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.repositories import companies as company_repo
from app.repositories import invoices as invoice_repo
from app.repositories import invoice_lines as invoice_lines_repo
from app.repositories import user_companies as user_company_repo


router = APIRouter(tags=["Invoices"])


_STATUS_CLASS_MAP = {
    "paid": "status--active",
    "sent": "status--invited",
    "pending": "status--invited",
    "issued": "status--invited",
    "draft": "status--invited",
    "overdue": "status--suspended",
    "past due": "status--suspended",
    "void": "status--invited",
    "cancelled": "status--invited",
    "xero": "status--xero",
}


@lru_cache(maxsize=1)
def _main():
    from app import main as main_module

    return main_module


async def _load_invoice_context(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_manage = bool(membership and membership.get("can_manage_invoices"))
    if not (is_super_admin or can_manage):
        return (
            user,
            membership,
            None,
            company_id,
            main_module.RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


def _format_invoice_records(
    records: list[dict[str, Any]], *, is_super_admin: bool = False
) -> tuple[list[dict[str, Any]], Decimal, int]:
    total_amount = Decimal("0.00")
    paid_count = 0
    today = datetime.now(timezone.utc).date()
    formatted: list[dict[str, Any]] = []
    for record in records:
        amount_value = record.get("amount")
        amount_decimal = amount_value if isinstance(amount_value, Decimal) else Decimal(str(amount_value or "0"))
        amount_decimal = amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount += amount_decimal
        status_text_raw = (record.get("status") or "").strip()
        status_slug = status_text_raw.lower()
        xero_invoice_id = (record.get("xero_invoice_id") or "").strip()
        if xero_invoice_id and status_slug not in {"paid", "void", "cancelled"}:
            status_slug = "xero"
            status_text_raw = "Xero"
        if status_slug == "paid":
            paid_count += 1
        status_class = _STATUS_CLASS_MAP.get(status_slug, "status--invited" if status_slug else "")
        created_value = record.get("created_at")
        created_iso = ""
        created_display = None
        if isinstance(created_value, datetime):
            if created_value.tzinfo is None:
                created_value = created_value.replace(tzinfo=timezone.utc)
            created_iso = created_value.astimezone(timezone.utc).isoformat()
            created_display = created_iso
        due_value = record.get("due_date")
        if isinstance(due_value, datetime):
            due_value = due_value.date()
        due_display = None
        due_iso = ""
        is_overdue = False
        if isinstance(due_value, date):
            due_display = due_value.strftime("%d %b %Y")
            due_iso = datetime.combine(due_value, time.min, tzinfo=timezone.utc).isoformat()
            is_overdue = bool(status_slug not in {"paid", "void", "cancelled"} and due_value < today)
        formatted.append(
            record
            | {
                "amount": amount_decimal,
                "amount_display": f"${amount_decimal:,.2f}",
                "created_display": created_display,
                "created_iso": created_iso,
                "created_sort": created_iso,
                "due_display": due_display,
                "due_iso": due_iso,
                "due_sort": due_iso,
                "status_display": status_text_raw.title() if status_text_raw else "—",
                "status_class": status_class,
                "status_slug": status_slug,
                "is_overdue": is_overdue,
                "xero_invoice_id": xero_invoice_id,
                "can_sync_to_xero": is_super_admin and not xero_invoice_id,
            }
        )
    return formatted, total_amount, paid_count


@router.api_route("/invoices", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def invoices_page(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_invoice_context(request)
    if redirect:
        return redirect
    records = await invoice_repo.list_company_invoices(company_id)
    formatted, total_amount, paid_count = _format_invoice_records(
        records, is_super_admin=bool(user.get("is_super_admin"))
    )
    unpaid_count = max(len(records) - paid_count, 0)
    total_amount_display = f"${total_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    status_options = sorted({invoice["status_slug"] for invoice in formatted if invoice["status_slug"]})
    extra = {
        "title": "Invoices",
        "invoices": formatted,
        "company": company,
        "has_invoices": bool(formatted),
        "total_amount_display": total_amount_display,
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "status_options": status_options,
        "can_delete_invoices": bool(user.get("is_super_admin")),
        "is_global_invoices": False,
        "invoice_table_id": "invoice",
        "can_sync_invoices_to_xero": bool(user.get("is_super_admin")),
    }
    return await main_module._render_template("invoices/index.html", request, user, extra=extra)


@router.api_route("/invoices/global", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def global_invoices_page(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return redirect
    if not bool(user.get("is_super_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global invoice review requires super admin access",
        )
    records = await invoice_repo.list_all_invoices()
    formatted, total_amount, paid_count = _format_invoice_records(records, is_super_admin=True)
    unpaid_count = max(len(records) - paid_count, 0)
    total_amount_display = f"${total_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    status_options = sorted({invoice["status_slug"] for invoice in formatted if invoice["status_slug"]})
    extra = {
        "title": "Global invoices",
        "invoices": formatted,
        "company": None,
        "has_invoices": bool(formatted),
        "total_amount_display": total_amount_display,
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "status_options": status_options,
        "can_delete_invoices": True,
        "is_global_invoices": True,
        "invoice_table_id": "invoice-global",
        "can_sync_invoices_to_xero": bool(user.get("is_super_admin")),
    }
    return await main_module._render_template("invoices/index.html", request, user, extra=extra)


@router.api_route("/invoices/{invoice_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def invoice_detail_page(request: Request, invoice_id: int):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_invoice_context(request)
    if redirect:
        return redirect
    invoice = await invoice_repo.get_invoice_by_id(invoice_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if not bool(user.get("is_super_admin")) and int(invoice.get("company_id", 0)) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if bool(user.get("is_super_admin")) and int(invoice.get("company_id", 0)) != company_id:
        company = await company_repo.get_company_by_id(int(invoice.get("company_id", 0)))
    lines = await invoice_lines_repo.list_invoice_lines(invoice_id)
    amount_value = invoice.get("amount")
    amount_decimal = (
        amount_value if isinstance(amount_value, Decimal) else Decimal(str(amount_value or "0"))
    )
    amount_decimal = amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    status_text_raw = (invoice.get("status") or "").strip()
    status_slug = status_text_raw.lower()
    xero_invoice_id = (invoice.get("xero_invoice_id") or "").strip()
    if xero_invoice_id and status_slug not in {"paid", "void", "cancelled"}:
        status_slug = "xero"
        status_text_raw = "Xero"
    status_class = _STATUS_CLASS_MAP.get(status_slug, "status--invited" if status_slug else "")
    created_value = invoice.get("created_at")
    created_iso = ""
    created_display = None
    if isinstance(created_value, datetime):
        if created_value.tzinfo is None:
            created_value = created_value.replace(tzinfo=timezone.utc)
        created_iso = created_value.astimezone(timezone.utc).isoformat()
        created_display = created_iso
    due_value = invoice.get("due_date")
    if isinstance(due_value, datetime):
        due_value = due_value.date()
    due_display = due_value.strftime("%d %b %Y") if isinstance(due_value, date) else None
    formatted_lines = []
    for line in lines:
        line_amount = line.get("amount")
        if line_amount is None:
            line_amount = Decimal("0.00")
        elif not isinstance(line_amount, Decimal):
            line_amount = Decimal(str(line_amount))
        line_amount = line_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        unit_amt = line.get("unit_amount")
        if unit_amt is None:
            unit_amt = Decimal("0.00")
        elif not isinstance(unit_amt, Decimal):
            unit_amt = Decimal(str(unit_amt))
        unit_amt = unit_amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        qty = line.get("quantity")
        if qty is None:
            qty = Decimal("1")
        elif not isinstance(qty, Decimal):
            qty = Decimal(str(qty))
        formatted_lines.append(
            dict(line)
            | {
                "amount": line_amount,
                "amount_display": f"${line_amount:,.2f}",
                "unit_amount": unit_amt,
                "unit_amount_display": f"${unit_amt:,.2f}",
                "quantity": qty,
            }
        )
    extra = {
        "title": f"Invoice {invoice['invoice_number']}",
        "invoice": dict(invoice)
        | {
            "amount": amount_decimal,
            "amount_display": f"${amount_decimal:,.2f}",
            "created_display": created_display,
            "created_iso": created_iso,
            "due_display": due_display,
            "status_display": status_text_raw.title() if status_text_raw else "—",
            "status_class": status_class,
            "status_slug": status_slug,
        },
        "lines": formatted_lines,
        "company": company,
        "has_lines": bool(formatted_lines),
        "can_delete_invoices": bool(user.get("is_super_admin")),
        "can_sync_invoices_to_xero": bool(user.get("is_super_admin")),
    }
    return await main_module._render_template("invoices/detail.html", request, user, extra=extra)


__all__ = ["router"]
