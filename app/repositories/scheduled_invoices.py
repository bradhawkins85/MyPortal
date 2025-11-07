"""Repository for managing scheduled invoices."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.core.database import db


def _normalize_scheduled_invoice(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a scheduled invoice row from the database."""
    return {
        "id": int(row["id"]),
        "customer_id": int(row["customer_id"]),
        "scheduled_for_date": row["scheduled_for_date"],
        "status": row["status"],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _normalize_invoice_line(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a scheduled invoice line row from the database."""
    return {
        "id": int(row["id"]),
        "scheduled_invoice_id": int(row["scheduled_invoice_id"]),
        "subscription_id": row["subscription_id"],
        "product_id": int(row["product_id"]),
        "term_start": row["term_start"],
        "term_end": row["term_end"],
        "price": Decimal(str(row["price"])),
        "created_at": row.get("created_at"),
    }


async def get_scheduled_invoice(invoice_id: int) -> dict[str, Any] | None:
    """Get a scheduled invoice by ID."""
    row = await db.fetch_one(
        """
        SELECT * FROM scheduled_invoices
        WHERE id = %s
        """,
        (invoice_id,),
    )
    if not row:
        return None
    return _normalize_scheduled_invoice(row)


async def get_scheduled_invoice_by_customer_and_date(
    customer_id: int, scheduled_date: date
) -> dict[str, Any] | None:
    """Get a scheduled invoice for a specific customer and date."""
    row = await db.fetch_one(
        """
        SELECT * FROM scheduled_invoices
        WHERE customer_id = %s AND scheduled_for_date = %s
        """,
        (customer_id, scheduled_date),
    )
    if not row:
        return None
    return _normalize_scheduled_invoice(row)


async def list_scheduled_invoices(
    *,
    customer_id: int | None = None,
    status: str | None = None,
    scheduled_before: date | None = None,
    scheduled_after: date | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List scheduled invoices with optional filters."""
    conditions: list[str] = []
    params: list[Any] = []
    
    if customer_id is not None:
        conditions.append("customer_id = %s")
        params.append(customer_id)
    
    if status is not None:
        conditions.append("status = %s")
        params.append(status)
    
    if scheduled_before is not None:
        conditions.append("scheduled_for_date < %s")
        params.append(scheduled_before)
    
    if scheduled_after is not None:
        conditions.append("scheduled_for_date > %s")
        params.append(scheduled_after)
    
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    query = f"""
        SELECT * FROM scheduled_invoices
        {where_clause}
        ORDER BY scheduled_for_date ASC
    """
    
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    
    rows = await db.fetch_all(query, tuple(params))
    return [_normalize_scheduled_invoice(row) for row in rows]


async def create_scheduled_invoice(
    customer_id: int, scheduled_for_date: date, status: str = "scheduled"
) -> dict[str, Any]:
    """Create a new scheduled invoice."""
    await db.execute(
        """
        INSERT INTO scheduled_invoices (customer_id, scheduled_for_date, status)
        VALUES (%s, %s, %s)
        """,
        (customer_id, scheduled_for_date, status),
    )
    
    created = await get_scheduled_invoice_by_customer_and_date(
        customer_id, scheduled_for_date
    )
    if not created:
        raise RuntimeError("Failed to create scheduled invoice")
    return created


async def update_scheduled_invoice_status(invoice_id: int, status: str) -> None:
    """Update the status of a scheduled invoice."""
    await db.execute(
        """
        UPDATE scheduled_invoices
        SET status = %s
        WHERE id = %s
        """,
        (status, invoice_id),
    )


async def get_invoice_lines(invoice_id: int) -> list[dict[str, Any]]:
    """Get all lines for a scheduled invoice."""
    rows = await db.fetch_all(
        """
        SELECT * FROM scheduled_invoice_lines
        WHERE scheduled_invoice_id = %s
        ORDER BY created_at
        """,
        (invoice_id,),
    )
    return [_normalize_invoice_line(row) for row in rows]


async def add_invoice_line(
    *,
    invoice_id: int,
    subscription_id: str,
    product_id: int,
    term_start: date,
    term_end: date,
    price: Decimal,
) -> None:
    """Add a line item to a scheduled invoice."""
    await db.execute(
        """
        INSERT INTO scheduled_invoice_lines (
            scheduled_invoice_id, subscription_id, product_id,
            term_start, term_end, price
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (invoice_id, subscription_id, product_id, term_start, term_end, price),
    )


async def delete_scheduled_invoice(invoice_id: int) -> None:
    """Delete a scheduled invoice and its lines."""
    await db.execute(
        "DELETE FROM scheduled_invoices WHERE id = %s",
        (invoice_id,),
    )
