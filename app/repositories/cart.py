from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    normalised["id"] = int(row["id"]) if row.get("id") is not None else None
    normalised["session_id"] = int(row["session_id"]) if row.get("session_id") is not None else None
    normalised["product_id"] = int(row["product_id"]) if row.get("product_id") is not None else None
    normalised["quantity"] = int(row["quantity"]) if row.get("quantity") is not None else 0
    normalised["product_name"] = row.get("product_name")
    normalised["product_sku"] = row.get("product_sku")
    normalised["product_vendor_sku"] = row.get("product_vendor_sku")
    normalised["product_description"] = row.get("product_description")
    normalised["product_image_url"] = row.get("product_image_url")
    unit_price = row.get("unit_price")
    if isinstance(unit_price, Decimal):
        normalised["unit_price"] = unit_price
    elif unit_price is None:
        normalised["unit_price"] = Decimal("0")
    else:
        normalised["unit_price"] = Decimal(str(unit_price))
    normalised["coterm_enabled"] = bool(row.get("coterm_enabled"))
    normalised["coterm_end_date"] = row.get("coterm_end_date")
    coterm_price = row.get("coterm_price")
    if isinstance(coterm_price, Decimal):
        normalised["coterm_price"] = coterm_price
    elif coterm_price is None:
        normalised["coterm_price"] = None
    else:
        normalised["coterm_price"] = Decimal(str(coterm_price))
    return normalised


async def get_item(session_id: int, product_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM shop_cart_items WHERE session_id = %s AND product_id = %s",
        (session_id, product_id),
    )
    return _normalise(row) if row else None


async def upsert_item(
    *,
    session_id: int,
    product_id: int,
    quantity: int,
    unit_price: Decimal,
    name: str,
    sku: str,
    vendor_sku: str | None,
    description: str | None,
    image_url: str | None,
    coterm_enabled: bool = False,
    coterm_end_date: Any | None = None,
    coterm_price: Decimal | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO shop_cart_items (
            session_id,
            product_id,
            quantity,
            unit_price,
            product_name,
            product_sku,
            product_vendor_sku,
            product_description,
            product_image_url,
            coterm_enabled,
            coterm_end_date,
            coterm_price
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            quantity = VALUES(quantity),
            unit_price = VALUES(unit_price),
            product_name = VALUES(product_name),
            product_sku = VALUES(product_sku),
            product_vendor_sku = VALUES(product_vendor_sku),
            product_description = VALUES(product_description),
            product_image_url = VALUES(product_image_url),
            coterm_enabled = VALUES(coterm_enabled),
            coterm_end_date = VALUES(coterm_end_date),
            coterm_price = VALUES(coterm_price)
        """,
        (
            session_id,
            product_id,
            quantity,
            unit_price,
            name,
            sku,
            vendor_sku,
            description,
            image_url,
            int(bool(coterm_enabled)),
            coterm_end_date,
            coterm_price,
        ),
    )


async def update_item_quantity(
    session_id: int,
    product_id: int,
    quantity: int,
) -> None:
    await db.execute(
        """
        UPDATE shop_cart_items
        SET quantity = %s
        WHERE session_id = %s AND product_id = %s
        """,
        (quantity, session_id, product_id),
    )


async def list_items(session_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM shop_cart_items
        WHERE session_id = %s
        ORDER BY created_at ASC
        """,
        (session_id,),
    )
    return [_normalise(row) for row in rows]


async def remove_items(session_id: int, product_ids: Iterable[int]) -> None:
    ids = [int(pid) for pid in product_ids]
    if not ids:
        return
    placeholders = ", ".join(["%s"] * len(ids))
    params: list[Any] = [session_id, *ids]
    # The IN placeholders are derived only from the validated product id count; values remain bound.
    await db.execute(  # nosec B608
        f"DELETE FROM shop_cart_items WHERE session_id = %s AND product_id IN ({placeholders})",  # nosec B608
        tuple(params),
    )


async def clear_cart(session_id: int) -> None:
    await db.execute(
        "DELETE FROM shop_cart_items WHERE session_id = %s",
        (session_id,),
    )


async def summarise_cart(session_id: int) -> dict[str, Any]:
    row = await db.fetch_one(
        """
        SELECT
            COUNT(*) AS item_count,
            COALESCE(SUM(quantity), 0) AS total_quantity,
            COALESCE(SUM(quantity * unit_price), 0) AS subtotal
        FROM shop_cart_items
        WHERE session_id = %s
        """,
        (session_id,),
    )
    item_count = int(row["item_count"]) if row and row.get("item_count") is not None else 0
    total_quantity = int(row["total_quantity"]) if row and row.get("total_quantity") is not None else 0
    raw_subtotal = row.get("subtotal") if row else None
    if isinstance(raw_subtotal, Decimal):
        subtotal = raw_subtotal
    elif raw_subtotal is None:
        subtotal = Decimal("0")
    else:
        subtotal = Decimal(str(raw_subtotal))
    return {
        "item_count": item_count,
        "total_quantity": total_quantity,
        "subtotal": subtotal,
    }
