from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

import aiofiles
import httpx
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element  # For type hints only
from defusedxml.ElementTree import fromstring, ParseError

from app.core.config import get_settings
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
            try:
                parsed = parsedate_to_datetime(candidate)
            except (TypeError, ValueError, IndexError):
                parsed = None
            if parsed:
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(timezone.utc)
                return parsed.date()
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


def _strip_xml_tag(name: str) -> str:
    if "}" in name:
        return name.split("}", 1)[1]
    return name


def _get_feed_value(element: Element, *names: str) -> str | None:
    """Retrieve a child or attribute value from a stock feed XML element."""

    if not names:
        return None
    lookup = {name.lower() for name in names}

    for attr_name, attr_value in element.attrib.items():
        if attr_name.lower() in lookup:
            candidate = (attr_value or "").strip()
            if candidate:
                return candidate

    for child in element:
        child_name = _strip_xml_tag(child.tag).lower()
        if child_name in lookup:
            text = (child.text or "").strip()
            if text:
                return text
    return None


def _coerce_feed_int(value: Any) -> int:
    decimal_value = _to_decimal(value, default=None)
    if decimal_value is not None:
        try:
            return int(decimal_value)
        except (ValueError, ArithmeticError):
            return 0
    try:
        candidate = str(value).strip()
    except Exception:
        return 0
    if not candidate:
        return 0
    try:
        return int(candidate)
    except ValueError:
        try:
            return int(float(candidate))
        except (TypeError, ValueError):
            return 0


def _parse_feed_item(element: Element) -> dict[str, Any] | None:
    sku = _get_feed_value(element, "StockCode", "stockcode", "stock_code", "sku")
    if not sku:
        return None

    sku_cleaned = sku.strip()
    if not sku_cleaned:
        return None

    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    product_name = _get_feed_value(element, "ProductName", "product_name") or ""
    product_name2 = _optional_text(
        _get_feed_value(element, "ProductName2", "product_name2")
    )
    category_name = _optional_text(
        _get_feed_value(element, "CategoryName", "category_name")
    )
    warranty_length = _optional_text(
        _get_feed_value(element, "WarrantyLength", "warranty_length")
    )
    manufacturer = _optional_text(
        _get_feed_value(element, "Manufacturer", "manufacturer")
    )
    image_url = _optional_text(
        _get_feed_value(element, "ImageUrl", "image_url")
    )

    pub_date_raw = _get_feed_value(element, "pubDate", "pub_date")
    pub_date = _parse_stock_date(pub_date_raw)

    return {
        "sku": sku_cleaned,
        "product_name": product_name,
        "product_name2": product_name2,
        "rrp": _to_decimal(_get_feed_value(element, "RRP", "rrp")),
        "category_name": category_name,
        "on_hand_nsw": _coerce_feed_int(
            _get_feed_value(element, "OnHandChanelNsw", "on_hand_nsw")
        ),
        "on_hand_qld": _coerce_feed_int(
            _get_feed_value(element, "OnHandChanelQld", "on_hand_qld")
        ),
        "on_hand_vic": _coerce_feed_int(
            _get_feed_value(element, "OnHandChanelVic", "on_hand_vic")
        ),
        "on_hand_sa": _coerce_feed_int(
            _get_feed_value(element, "OnHandChanelSa", "on_hand_sa")
        ),
        "dbp": _to_decimal(_get_feed_value(element, "DBP", "dbp")),
        "weight": _to_decimal(_get_feed_value(element, "Weight", "weight")),
        "length": _to_decimal(_get_feed_value(element, "Length", "length")),
        "width": _to_decimal(_get_feed_value(element, "Width", "width")),
        "height": _to_decimal(_get_feed_value(element, "Height", "height")),
        "pub_date": pub_date,
        "warranty_length": warranty_length,
        "manufacturer": manufacturer,
        "image_url": image_url,
    }


def _parse_stock_feed_xml(payload: str) -> list[dict[str, Any]]:
    try:
        root = fromstring(payload)
    except ParseError as exc:
        raise ValueError("Invalid stock feed XML") from exc

    items: list[dict[str, Any]] = []
    for element in root.iter():
        if _strip_xml_tag(element.tag).lower() == "item":
            parsed = _parse_feed_item(element)
            if parsed:
                items.append(parsed)

    if not items and _strip_xml_tag(root.tag).lower() == "items":
        for element in root:
            parsed = _parse_feed_item(element)
            if parsed:
                items.append(parsed)

    return items


async def update_stock_feed() -> int:
    """Download the stock feed and persist it for later product updates."""

    settings = get_settings()
    url = getattr(settings, "stock_feed_url", None)
    if not url:
        raise ValueError("STOCK_FEED_URL is not configured")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(str(url), follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log_error("Failed to download stock feed", url=str(url), error=str(exc))
        raise

    try:
        items = _parse_stock_feed_xml(response.text)
    except ValueError as exc:
        log_error("Failed to parse stock feed", error=str(exc))
        raise

    await stock_feed_repo.replace_feed(items)
    log_info("Stock feed updated", item_count=len(items))
    return len(items)


async def _get_or_create_category_hierarchy(category_path: str) -> int | None:
    """
    Parse a category path delimited by ' - ' and create/retrieve the hierarchy.
    
    For example, "Electronics - Computers - Laptops" will:
    1. Create/get "Electronics" (parent)
    2. Create/get "Computers" as child of "Electronics"
    3. Create/get "Laptops" as child of "Computers"
    4. Return the ID of the final category ("Laptops")
    
    Args:
        category_path: A string with category names separated by ' - '
        
    Returns:
        The ID of the final (deepest) category in the hierarchy, or None if invalid
    """
    if not category_path or not category_path.strip():
        return None
    
    # Split by ' - ' delimiter and clean up each part
    parts = [part.strip() for part in category_path.split(" - ")]
    parts = [part for part in parts if part]  # Remove empty parts
    
    if not parts:
        return None
    
    # Fetch all categories once to avoid N+1 queries
    all_categories = await shop_repo.list_all_categories_flat()
    
    # Build lookup dictionary for O(1) access using (name, parent_id) as key
    category_lookup: dict[tuple[str, int | None], dict[str, Any]] = {}
    for cat in all_categories:
        key = (cat["name"], cat.get("parent_id"))
        category_lookup[key] = cat
    
    parent_id: int | None = None
    
    for category_name in parts:
        # Look up existing category with this name and parent
        lookup_key = (category_name, parent_id)
        matching_category = category_lookup.get(lookup_key)
        
        if matching_category:
            # Found exact match (same name and parent)
            category_id = int(matching_category["id"])
        else:
            # Create new category with appropriate parent
            try:
                category_id = await shop_repo.create_category(
                    name=category_name,
                    parent_id=parent_id,
                    display_order=0
                )
                # Add newly created category to our lookup dictionary
                new_category = {
                    "id": category_id,
                    "name": category_name,
                    "parent_id": parent_id,
                    "display_order": 0,
                }
                category_lookup[lookup_key] = new_category
            except Exception as exc:  # pragma: no cover - defensive guard
                log_error(
                    "Failed to create category in hierarchy",
                    category=category_name,
                    parent_id=parent_id,
                    error=str(exc),
                )
                return None
        
        # This category becomes the parent for the next level
        parent_id = category_id
    
    return parent_id


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
        # Parse and create category hierarchy (supports both single and multi-level categories)
        category_id = await _get_or_create_category_hierarchy(category_name)

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
