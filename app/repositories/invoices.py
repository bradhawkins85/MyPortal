from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.core.database import db

_ALLOWED_PATCH_COLUMNS = frozenset({
    "company_id", "invoice_number", "amount", "due_date", "status",
    "xero_invoice_id", "xero_invoice_number", "synced_to_xero_at",
    "approval_required", "approved_by", "approved_at",
    "billing_adjustment_type", "billing_adjustment_amount", "billing_adjustment_reason",
    "xero_sync_error", "xero_sync_attempted_at",
})


def _normalise_invoice(row: dict[str, Any]) -> dict[str, Any]:
    invoice = dict(row)
    if "id" in invoice and invoice["id"] is not None:
        invoice["id"] = int(invoice["id"])
    if "company_id" in invoice and invoice["company_id"] is not None:
        invoice["company_id"] = int(invoice["company_id"])
    amount = invoice.get("amount")
    if amount is not None:
        invoice["amount"] = Decimal(str(amount))
    adjustment_amount = invoice.get("billing_adjustment_amount")
    if adjustment_amount is not None:
        invoice["billing_adjustment_amount"] = Decimal(str(adjustment_amount))
    due_date = invoice.get("due_date")
    if isinstance(due_date, datetime):
        invoice["due_date"] = due_date.date()
    elif due_date is None or isinstance(due_date, date):
        invoice["due_date"] = due_date
    approval_required = invoice.get("approval_required")
    if approval_required is not None:
        invoice["approval_required"] = bool(approval_required)
    for key in ("created_at", "synced_to_xero_at", "approved_at", "xero_sync_attempted_at"):
        value = invoice.get(key)
        if value is not None and not isinstance(value, datetime):
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            invoice[key] = parsed
        if isinstance(invoice.get(key), datetime) and invoice[key].tzinfo is None:
            invoice[key] = invoice[key].replace(tzinfo=timezone.utc)
    approved_by = invoice.get("approved_by")
    if approved_by is not None:
        invoice["approved_by"] = int(approved_by)
    return invoice


async def list_company_invoices(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM invoices WHERE company_id = %s ORDER BY due_date DESC, invoice_number",
        (company_id,),
    )
    return [_normalise_invoice(row) for row in rows]


async def list_xero_sync_exceptions(company_id: int | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where_clauses = [
        "COALESCE(TRIM(xero_invoice_id), '') = ''",
        "COALESCE(TRIM(xero_sync_error), '') <> ''",
    ]
    if company_id is not None:
        where_clauses.append("company_id = %s")
        params.append(company_id)
    rows = await db.fetch_all(
        f"""
        SELECT *
        FROM invoices
        WHERE {' AND '.join(where_clauses)}
        ORDER BY xero_sync_attempted_at DESC, id DESC
        """,
        tuple(params),
    )
    return [_normalise_invoice(row) for row in rows]


async def list_all_invoices() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT invoices.*, companies.name AS company_name
        FROM invoices
        LEFT JOIN companies ON companies.id = invoices.company_id
        ORDER BY invoices.due_date DESC, invoices.invoice_number
        """
    )
    return [_normalise_invoice(row) for row in rows]


async def get_invoice_by_id(invoice_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    return _normalise_invoice(row) if row else None


async def get_invoice_by_xero_invoice_id(xero_invoice_id: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM invoices WHERE xero_invoice_id = %s LIMIT 1",
        (xero_invoice_id,),
    )
    return _normalise_invoice(row) if row else None


async def get_invoice_by_number(invoice_number: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM invoices WHERE invoice_number = %s LIMIT 1",
        (invoice_number,),
    )
    return _normalise_invoice(row) if row else None


async def list_unsynced_company_invoices(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM invoices
        WHERE company_id = %s
          AND COALESCE(TRIM(xero_invoice_id), '') = ''
        ORDER BY due_date ASC, invoice_number ASC, id ASC
        """,
        (company_id,),
    )
    return [_normalise_invoice(row) for row in rows]


async def create_invoice(
    *,
    company_id: int,
    invoice_number: str,
    amount: Decimal,
    due_date: date | None,
    status: str | None,
    approval_required: bool = False,
    approved_by: int | None = None,
    approved_at: datetime | None = None,
    billing_adjustment_type: str | None = None,
    billing_adjustment_amount: Decimal | None = None,
    billing_adjustment_reason: str | None = None,
    xero_sync_error: str | None = None,
    xero_sync_attempted_at: datetime | None = None,
) -> dict[str, Any]:
    invoice_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO invoices (
            company_id,
            invoice_number,
            amount,
            due_date,
            status,
            approval_required,
            approved_by,
            approved_at,
            billing_adjustment_type,
            billing_adjustment_amount,
            billing_adjustment_reason,
            xero_sync_error,
            xero_sync_attempted_at,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            invoice_number,
            amount,
            due_date,
            status,
            int(bool(approval_required)),
            approved_by,
            approved_at,
            billing_adjustment_type,
            billing_adjustment_amount,
            billing_adjustment_reason,
            xero_sync_error,
            xero_sync_attempted_at,
            datetime.now(timezone.utc),
        ),
    )
    if not invoice_id:
        raise RuntimeError("Failed to create invoice")
    row = await db.fetch_one("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    if not row:
        raise RuntimeError("Failed to retrieve created invoice")
    return _normalise_invoice(row)


async def update_invoice(
    invoice_id: int,
    *,
    company_id: int,
    invoice_number: str,
    amount: Decimal,
    due_date: date | None,
    status: str | None,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE invoices
        SET company_id = %s, invoice_number = %s, amount = %s, due_date = %s, status = %s
        WHERE id = %s
        """,
        (company_id, invoice_number, amount, due_date, status, invoice_id),
    )
    updated = await get_invoice_by_id(invoice_id)
    if not updated:
        raise ValueError("Invoice not found after update")
    return updated


async def patch_invoice(invoice_id: int, **updates: Any) -> dict[str, Any]:
    unknown = set(updates) - _ALLOWED_PATCH_COLUMNS
    if unknown:
        raise ValueError(f"Unsupported invoice fields: {', '.join(sorted(unknown))}")
    if not updates:
        existing = await get_invoice_by_id(invoice_id)
        if not existing:
            raise ValueError("Invoice not found")
        return existing
    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [invoice_id]
    # Columns are produced by the explicit invoice patch allowlist above; values remain bound.
    await db.execute(  # nosec B608
        f"UPDATE invoices SET {columns} WHERE id = %s",  # nosec B608
        tuple(params),
    )
    updated = await get_invoice_by_id(invoice_id)
    if not updated:
        raise ValueError("Invoice not found after update")
    return updated


async def delete_invoice(invoice_id: int) -> None:
    await db.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))


async def get_max_invoice_seq(prefix: str) -> int:
    """Return the highest sequence number used for invoice numbers starting with *prefix*.

    Invoice numbers follow the pattern ``{prefix}NNNN`` (e.g. ``INV-202603-0012``).
    Returns 0 when no matching invoice exists yet.
    """
    row = await db.fetch_one(
        """
        SELECT MAX(CAST(SUBSTRING(invoice_number, %s) AS UNSIGNED)) AS max_seq
        FROM invoices
        WHERE invoice_number LIKE %s
        """,
        (len(prefix) + 1, f"{prefix}%"),
    )
    if not row:
        return 0
    val = row.get("max_seq")
    if val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
