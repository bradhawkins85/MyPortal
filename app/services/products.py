from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

import aiofiles
import httpx

from app.core.logging import log_error, log_info
from app.repositories import shop as shop_repo
from app.repositories import stock_feed as stock_feed_repo


class _ImageTooLargeError(Exception):
    """Raised when a downloaded product image exceeds the permitted size."""


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PRIVATE_UPLOADS_PATH = _PROJECT_ROOT / "private_uploads"
_SHOP_UPLOADS_PATH = _PRIVATE_UPLOADS_PATH / "shop"
_MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
_ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMAGE_CONTENT_TYPE_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

_PRIVATE_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)
_SHOP_UPLOADS_PATH.mkdir(parents=True, exist_ok=True)


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


async def _download_product_image(image_url: str) -> str | None:
    """Download a product image from the stock feed into the private uploads dir."""

    candidate = image_url.strip()
    if not candidate:
        return None

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        log_error("Rejected non-HTTP product image URL from feed", url=candidate)
        return None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            async with client.stream("GET", candidate, follow_redirects=True) as response:
                if response.status_code != httpx.codes.OK:
                    log_error(
                        "Failed to download product image from feed",
                        url=candidate,
                        status_code=response.status_code,
                    )
                    return None

                content_type_header = response.headers.get("Content-Type", "")
                content_type = content_type_header.split(";", 1)[0].strip().lower()
                suffix = Path(parsed.path or "").suffix.lower()
                if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
                    suffix = _IMAGE_CONTENT_TYPE_MAP.get(content_type, suffix)
                if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
                    log_error(
                        "Unsupported product image type from feed",
                        url=candidate,
                        content_type=content_type or "unknown",
                    )
                    return None

                stored_name = f"{uuid4().hex}{suffix}"
                destination = _SHOP_UPLOADS_PATH / stored_name
                total_size = 0

                try:
                    async with aiofiles.open(destination, "wb") as buffer:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            if not chunk:
                                continue
                            total_size += len(chunk)
                            if total_size > _MAX_IMAGE_SIZE:
                                raise _ImageTooLargeError
                            await buffer.write(chunk)
                except _ImageTooLargeError:
                    destination.unlink(missing_ok=True)
                    log_error(
                        "Feed product image exceeds maximum size",
                        url=candidate,
                        limit=_MAX_IMAGE_SIZE,
                    )
                    return None
                except Exception as exc:  # pragma: no cover - defensive guard
                    destination.unlink(missing_ok=True)
                    log_error(
                        "Failed to persist downloaded product image",
                        url=candidate,
                        error=str(exc),
                    )
                    return None

                return f"/uploads/shop/{stored_name}"
    except httpx.HTTPError as exc:
        log_error(
            "Error downloading product image from feed",
            url=candidate,
            error=str(exc),
        )
        return None


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

    rrp = _to_decimal(item.get("rrp"))
    existing_price = (
        _to_decimal(current_product.get("price")) if current_product else None
    )
    price: Decimal | None = None
    if existing_price is not None and existing_price > 0:
        price = existing_price
    elif rrp is not None and rrp > 0:
        price = rrp
    else:
        price = existing_price or rrp or Decimal("0")

    existing_vip_price = (
        _to_decimal(current_product.get("vip_price")) if current_product else None
    )
    vip_price: Decimal | None = None
    if existing_vip_price is not None and existing_vip_price > 0:
        vip_price = existing_vip_price
    elif rrp is not None and rrp > 0:
        vip_price = rrp
    else:
        vip_price = price

    if vip_price is None:
        vip_price = Decimal("0")

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

    existing_image_url = (
        str(current_product.get("image_url") or "").strip()
        if current_product
        else ""
    )
    feed_image_url = str(item.get("image_url") or "").strip()
    image_url: str | None = None
    if existing_image_url:
        image_url = None
    elif feed_image_url:
        image_url = await _download_product_image(feed_image_url)

    await shop_repo.upsert_product_from_feed(
        name=name[:255],
        sku=code,
        vendor_sku=code,
        description=description,
        image_url=image_url,
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
