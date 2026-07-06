from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Sequence

from app.core.database import db


def _bool_to_tinyint(value: bool) -> int:
    """Convert boolean to MySQL TINYINT(1) format."""
    return 1 if value else 0


async def list_company_recurring_invoice_items(company_id: int) -> Sequence[dict[str, Any]]:
    """List all recurring invoice items for a company."""
    rows = await db.fetch_all(
        """
        SELECT id, company_id, product_code, description_template, qty_expression,
               price_override, active, billing_frequency, billing_interval,
               start_date, end_date, last_billed_at, created_at, updated_at
        FROM company_recurring_invoice_items
        WHERE company_id = %s
        ORDER BY created_at DESC
        """,
        (company_id,),
    )
    return rows


async def get_recurring_invoice_item(item_id: int) -> Optional[dict[str, Any]]:
    """Get a single recurring invoice item by ID."""
    row = await db.fetch_one(
        """
        SELECT id, company_id, product_code, description_template, qty_expression,
               price_override, active, billing_frequency, billing_interval,
               start_date, end_date, last_billed_at, created_at, updated_at
        FROM company_recurring_invoice_items
        WHERE id = %s
        """,
        (item_id,),
    )
    return row


async def create_recurring_invoice_item(
    company_id: int,
    product_code: str,
    description_template: str,
    qty_expression: str,
    price_override: Optional[float] = None,
    active: bool = True,
    billing_frequency: str = "every_run",
    billing_interval: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict[str, Any]:
    """Create a new recurring invoice item."""
    item_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO company_recurring_invoice_items
        (company_id, product_code, description_template, qty_expression, price_override, active,
         billing_frequency, billing_interval, start_date, end_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            product_code,
            description_template,
            qty_expression,
            price_override,
            _bool_to_tinyint(active),
            billing_frequency,
            billing_interval,
            start_date,
            end_date,
        ),
    )
    row = await db.fetch_one(
        """
        SELECT id, company_id, product_code, description_template, qty_expression,
               price_override, active, billing_frequency, billing_interval,
               start_date, end_date, last_billed_at, created_at, updated_at
        FROM company_recurring_invoice_items
        WHERE id = %s
        """,
        (item_id,),
    )
    if not row:
        raise RuntimeError("Failed to create recurring invoice item")
    return row


async def update_recurring_invoice_item(
    item_id: int,
    product_code: Optional[str] = None,
    description_template: Optional[str] = None,
    qty_expression: Optional[str] = None,
    price_override: Optional[float] = None,
    active: Optional[bool] = None,
    billing_frequency: Optional[str] = None,
    billing_interval: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    clear_price_override: bool = False,
    clear_billing_interval: bool = False,
    clear_start_date: bool = False,
    clear_end_date: bool = False,
) -> Optional[dict[str, Any]]:
    """Update a recurring invoice item."""
    updates = []
    params = []
    
    if product_code is not None:
        updates.append("product_code = %s")
        params.append(product_code)
    
    if description_template is not None:
        updates.append("description_template = %s")
        params.append(description_template)
    
    if qty_expression is not None:
        updates.append("qty_expression = %s")
        params.append(qty_expression)
    
    if clear_price_override:
        updates.append("price_override = NULL")
    elif price_override is not None:
        updates.append("price_override = %s")
        params.append(price_override)
    
    if active is not None:
        updates.append("active = %s")
        params.append(_bool_to_tinyint(active))

    if billing_frequency is not None:
        updates.append("billing_frequency = %s")
        params.append(billing_frequency)

    if clear_billing_interval:
        updates.append("billing_interval = NULL")
    elif billing_interval is not None:
        updates.append("billing_interval = %s")
        params.append(billing_interval)

    if clear_start_date:
        updates.append("start_date = NULL")
    elif start_date is not None:
        updates.append("start_date = %s")
        params.append(start_date)

    if clear_end_date:
        updates.append("end_date = NULL")
    elif end_date is not None:
        updates.append("end_date = %s")
        params.append(end_date)
    
    if not updates:
        return await get_recurring_invoice_item(item_id)
    
    params.append(item_id)
    await db.execute(
        f"UPDATE company_recurring_invoice_items SET {', '.join(updates)} WHERE id = %s",
        tuple(params),
    )
    
    return await get_recurring_invoice_item(item_id)


async def delete_recurring_invoice_item(item_id: int) -> None:
    """Delete a recurring invoice item."""
    await db.execute(
        "DELETE FROM company_recurring_invoice_items WHERE id = %s",
        (item_id,),
    )


async def mark_recurring_invoice_items_billed(
    item_ids: Sequence[int],
    *,
    billed_at: Optional[datetime] = None,
) -> None:
    """Record the UTC timestamp when recurring items were successfully invoiced."""
    unique_ids = sorted({int(item_id) for item_id in item_ids if item_id})
    if not unique_ids:
        return
    placeholders = ", ".join(["%s"] * len(unique_ids))
    await db.execute(
        f"""
        UPDATE company_recurring_invoice_items
        SET last_billed_at = %s
        WHERE id IN ({placeholders})
        """,
        tuple([billed_at or datetime.utcnow(), *unique_ids]),
    )
