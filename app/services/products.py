from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

from app.core.logging import log_error, log_info
from app.repositories import shop as shop_repo
from app.repositories import stock_feed as stock_feed_repo


def _to_decimal(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _parse_stock_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        normalised = candidate
        if normalised.endswith("Z"):
            normalised = normalised[:-1] + "+00:00"
        tz_match = re.search(r"([+-])(\d{2})(\d{2})$", normalised)
        if tz_match and ":" not in normalised[tz_match.start() :]:
            normalised = (
                normalised[: tz_match.start()]
                + tz_match.group(1)
                + tz_match.group(2)
                + ":"
                + tz_match.group(3)
            )
        try:
            return datetime.fromisoformat(normalised).date()
        except ValueError:
            trimmed = candidate[:10]
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(trimmed, fmt).date()
                except ValueError:
                    continue
    return None


def _normalise_name(feed_name: str, product: Mapping[str, Any] | None) -> str:
    if not product:
        return feed_name or ""
    existing_name = str(product.get("name") or "").strip()
    if not existing_name:
        return feed_name or ""
    existing_sku = str(
        product.get("vendor_sku") or product.get("sku") or ""
    ).strip()
    if existing_sku and existing_name.lower() != existing_sku.lower():
        return existing_name
    return feed_name or existing_name or existing_sku or ""


async def _process_feed_item(
    item: Mapping[str, Any],
    existing_product: Mapping[str, Any] | None,
) -> bool:
    code = str(item.get("sku") or "").strip()
    if not code:
        return False

    current_product = existing_product or await shop_repo.get_product_by_sku(
        code, include_archived=True
    )

    feed_name = str(item.get("product_name") or "").strip()
    description = str(item.get("product_name2") or "").strip() or None

    price = _to_decimal(
        current_product.get("price") if current_product else item.get("rrp"),
        default=Decimal("0"),
    )
    if price is None:
        price = Decimal("0")
    vip_price = _to_decimal(
        current_product.get("vip_price") if current_product else None,
        default=price,
    )
    if vip_price is None:
        vip_price = price

    category_id: int | None = None
    category_name = str(item.get("category_name") or "").strip()
    if category_name:
        category = await shop_repo.get_category_by_name(category_name)
        if category:
            category_id = int(category["id"])
        else:
            try:
                category_id = await shop_repo.create_category(category_name)
            except Exception as exc:  # pragma: no cover - defensive guard
                log_error(
                    "Failed to create product category from feed",
                    category=category_name,
                    error=str(exc),
                )

    stock_nsw = int(item.get("on_hand_nsw") or 0)
    stock_qld = int(item.get("on_hand_qld") or 0)
    stock_vic = int(item.get("on_hand_vic") or 0)
    stock_sa = int(item.get("on_hand_sa") or 0)
    stock_total = stock_nsw + stock_qld + stock_vic + stock_sa

    buy_price = _to_decimal(item.get("dbp"))
    weight = _to_decimal(item.get("weight"))
    length = _to_decimal(item.get("length"))
    width = _to_decimal(item.get("width"))
    height = _to_decimal(item.get("height"))
    stock_at = _parse_stock_date(item.get("pub_date"))
    raw_warranty = item.get("warranty_length")
    warranty_length = str(raw_warranty).strip() if raw_warranty else None
    if warranty_length:
        warranty_length = warranty_length[:255]
    raw_manufacturer = item.get("manufacturer")
    manufacturer = str(raw_manufacturer).strip() if raw_manufacturer else None
    if manufacturer:
        manufacturer = manufacturer[:255]

    name = _normalise_name(feed_name, current_product)
    if not name:
        name = code

    await shop_repo.upsert_product_from_feed(
        name=name[:255],
        sku=code,
        vendor_sku=code,
        description=description,
        image_url=None,
        price=price,
        vip_price=vip_price,
        stock=stock_total,
        category_id=category_id,
        stock_nsw=stock_nsw,
        stock_qld=stock_qld,
        stock_vic=stock_vic,
        stock_sa=stock_sa,
        buy_price=buy_price,
        weight=weight,
        length=length,
        width=width,
        height=height,
        stock_at=stock_at,
        warranty_length=warranty_length,
        manufacturer=manufacturer,
    )
    return True


async def import_product_by_vendor_sku(vendor_sku: str) -> bool:
    """Import a single product from the stock feed by vendor SKU."""

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        return False

    item = await stock_feed_repo.get_item_by_sku(cleaned_vendor_sku)
    if not item:
        log_info(
            "Stock feed item not found for vendor SKU import",
            vendor_sku=cleaned_vendor_sku,
        )
        return False

    existing_product = await shop_repo.get_product_by_sku(
        cleaned_vendor_sku, include_archived=True
    )

    try:
        processed = await _process_feed_item(item, existing_product)
    except Exception as exc:
        log_error(
            "Failed to import product from stock feed",
            vendor_sku=cleaned_vendor_sku,
            error=str(exc),
        )
        raise

    if processed:
        existing_id = None
        if existing_product and "id" in existing_product:
            try:
                existing_id = int(existing_product["id"])
            except (TypeError, ValueError):  # pragma: no cover - defensive
                existing_id = None
        log_info(
            "Imported product from stock feed",
            vendor_sku=cleaned_vendor_sku,
            existing_product_id=existing_id,
        )

    return processed


async def update_products_from_feed() -> None:
    products = await shop_repo.list_all_products(include_archived=True)
    processed = 0
    updated = 0

    for product in products:
        processed += 1
        sku = str(product.get("vendor_sku") or product.get("sku") or "").strip()
        if not sku:
            continue
        item = await stock_feed_repo.get_item_by_sku(sku)
        if not item:
            continue
        try:
            if await _process_feed_item(item, product):
                updated += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to process stock feed item",
                sku=sku,
                error=str(exc),
            )

    log_info(
        "Product feed synchronisation completed",
        processed=processed,
        updated=updated,
    )
