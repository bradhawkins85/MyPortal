from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app.core.database import db


def _normalise_invoice(row: dict[str, Any]) -> dict[str, Any]:
    invoice = dict(row)
    if "id" in invoice and invoice["id"] is not None:
        invoice["id"] = int(invoice["id"])
    if "company_id" in invoice and invoice["company_id"] is not None:
        invoice["company_id"] = int(invoice["company_id"])
    amount = invoice.get("amount")
    if amount is not None:
        invoice["amount"] = Decimal(str(amount))
    due_date = invoice.get("due_date")
    if isinstance(due_date, datetime):
        invoice["due_date"] = due_date.date()
    elif due_date is None or isinstance(due_date, date):
        invoice["due_date"] = due_date
    return invoice


async def list_company_invoices(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM invoices WHERE company_id = %s ORDER BY due_date DESC, invoice_number",
        (company_id,),
    )
    return [_normalise_invoice(row) for row in rows]


async def get_invoice_by_id(invoice_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    return _normalise_invoice(row) if row else None


async def create_invoice(
    *,
    company_id: int,
    invoice_number: str,
    amount: Decimal,
    due_date: date | None,
    status: str | None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO invoices (company_id, invoice_number, amount, due_date, status)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (company_id, invoice_number, amount, due_date, status),
    )
    row = await db.fetch_one("SELECT * FROM invoices WHERE id = LAST_INSERT_ID()")
    if not row:
        raise RuntimeError("Failed to create invoice")
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
    if not updates:
        existing = await get_invoice_by_id(invoice_id)
        if not existing:
            raise ValueError("Invoice not found")
        return existing
    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [invoice_id]
    await db.execute(
        f"UPDATE invoices SET {columns} WHERE id = %s",
        tuple(params),
    )
    updated = await get_invoice_by_id(invoice_id)
    if not updated:
        raise ValueError("Invoice not found after update")
    return updated


async def delete_invoice(invoice_id: int) -> None:
    await db.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))
