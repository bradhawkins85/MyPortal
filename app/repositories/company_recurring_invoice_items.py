from __future__ import annotations

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
               price_override, active, created_at, updated_at
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
               price_override, active, created_at, updated_at
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
) -> dict[str, Any]:
    """Create a new recurring invoice item."""
    await db.execute(
        """
        INSERT INTO company_recurring_invoice_items
        (company_id, product_code, description_template, qty_expression, price_override, active)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (company_id, product_code, description_template, qty_expression, price_override, _bool_to_tinyint(active)),
    )
    row = await db.fetch_one(
        """
        SELECT id, company_id, product_code, description_template, qty_expression,
               price_override, active, created_at, updated_at
        FROM company_recurring_invoice_items
        WHERE id = LAST_INSERT_ID()
        """
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
    
    if price_override is not None:
        updates.append("price_override = %s")
        params.append(price_override)
    
    if active is not None:
        updates.append("active = %s")
        params.append(_bool_to_tinyint(active))
    
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
