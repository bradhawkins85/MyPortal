from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.repositories import shop as shop_repo
from app.services.notifications import emit_notification


def get_product_price(product: Mapping[str, Any], *, is_vip: bool = False) -> Decimal:
    """Return the appropriate price for a product.

    For subscription products the commitment-specific price takes precedence:
    - monthly commitment  → ``price_monthly_commitment``
    - annual / monthly payment → ``price_annual_monthly_payment``
    - annual / annual payment  → ``price_annual_annual_payment``

    If no matching commitment price is found the function falls back to the VIP
    price (when *is_vip* is ``True`` and ``vip_price`` is set) and finally to
    the standard ``price`` field.
    """
    commitment_type = product.get("commitment_type")
    payment_frequency = product.get("payment_frequency")

    if commitment_type == "monthly":
        custom_price = product.get("price_monthly_commitment")
        if custom_price is not None:
            return Decimal(str(custom_price))
    elif commitment_type == "annual":
        if payment_frequency == "monthly":
            custom_price = product.get("price_annual_monthly_payment")
            if custom_price is not None:
                return Decimal(str(custom_price))
        elif payment_frequency == "annual":
            custom_price = product.get("price_annual_annual_payment")
            if custom_price is not None:
                return Decimal(str(custom_price))

    if is_vip and product.get("vip_price") is not None:
        return Decimal(str(product["vip_price"]))
    return Decimal(str(product.get("price") or 0))


_DBP_MARGIN = Decimal("1.1")


def is_price_below_dbp_threshold(
    product: Mapping[str, Any], *, is_vip: bool = False
) -> bool:
    """Return True if the effective sale price is below DBP * 1.1.

    The effective sale price is determined by :func:`get_product_price` using
    the given *is_vip* flag.  If the product has no ``buy_price`` (DBP) the
    function always returns ``False`` — there is nothing to compare against.
    """
    buy_price_raw = product.get("buy_price")
    if buy_price_raw is None:
        return False
    try:
        buy_price = Decimal(str(buy_price_raw))
    except (InvalidOperation, ValueError):
        return False
    if buy_price <= 0:
        return False
    threshold = buy_price * _DBP_MARGIN
    sale_price = get_product_price(product, is_vip=is_vip)
    return sale_price < threshold


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
