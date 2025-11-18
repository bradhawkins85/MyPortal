from __future__ import annotations

from typing import Any, Mapping

from app.repositories import shop as shop_repo
from app.services.notifications import emit_notification


async def maybe_send_stock_notification_by_id(
    product_id: int,
    previous_stock: int | None,
    new_stock: int | None,
) -> None:
    if previous_stock is None or new_stock is None:
        return
    if (previous_stock > 0 and new_stock > 0) or (previous_stock <= 0 and new_stock <= 0):
        return
    product = await shop_repo.get_product_by_id(
        product_id,
        include_archived=True,
    )
    if not product:
        return None

    product_name = str(product.get('name') or 'Product')
    product_sku = str(product.get('sku') or 'SKU')
    message: str | None = None
    if previous_stock > 0 and new_stock <= 0:
        message = f"{product_name} ({product_sku}) is now out of stock (was {previous_stock})."
    elif previous_stock <= 0 and new_stock > 0:
        message = f"{product_name} ({product_sku}) is back in stock with {max(new_stock, 0)} available."

    if message:
        await emit_notification(
            event_type="shop.stock_notification",
            message=message,
            metadata={
                "product_id": product_id,
                "previous_stock": previous_stock,
                "new_stock": new_stock,
            },
        )
