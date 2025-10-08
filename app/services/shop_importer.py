from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import aiofiles
import httpx
from fastapi import status

from app.core.logging import log_error, log_info
from app.repositories import shop as shop_repo
from app.services.file_storage import sanitize_filename

_ALLOWED_IMAGE_MIME_MAP: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ProductImportError(RuntimeError):
    """Raised when a product import cannot be completed."""

    def __init__(self, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def import_product_by_vendor_sku(
    vendor_sku: str,
    *,
    uploads_root: Path,
) -> dict[str, Any]:
    """Import or update a product using stock feed data for *vendor_sku*."""

    normalised_sku = vendor_sku.strip()
    if not normalised_sku:
        raise ProductImportError("Vendor SKU cannot be empty")

    feed_item = await shop_repo.get_stock_feed_item_by_sku(normalised_sku)
    if not feed_item:
        raise ProductImportError(
            "No stock feed entry exists for the supplied vendor SKU.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    code = str(feed_item.get("sku") or normalised_sku).strip()
    if not code:
        raise ProductImportError("Stock feed entry is missing a SKU")

    existing = await shop_repo.get_product_by_sku(code)

    feed_name = _clean_string(feed_item.get("product_name"))
    description = _clean_string(feed_item.get("product_name2"))

    price = _decimal_from_value(
        existing.get("price") if existing else feed_item.get("rrp"),
        fallback=Decimal("0.00"),
    )
    vip_price = _decimal_from_value(
        existing.get("vip_price") if existing else feed_item.get("rrp"),
        fallback=price,
    )
    buy_price = _decimal_from_value(feed_item.get("dbp"))
    weight = _decimal_from_value(feed_item.get("weight"), quantise=False)
    length = _decimal_from_value(feed_item.get("length"), quantise=False)
    width = _decimal_from_value(feed_item.get("width"), quantise=False)
    height = _decimal_from_value(feed_item.get("height"), quantise=False)

    stock_nsw = int(feed_item.get("on_hand_nsw") or 0)
    stock_qld = int(feed_item.get("on_hand_qld") or 0)
    stock_vic = int(feed_item.get("on_hand_vic") or 0)
    stock_sa = int(feed_item.get("on_hand_sa") or 0)
    stock_total = stock_nsw + stock_qld + stock_vic + stock_sa

    category_name = _clean_string(feed_item.get("category_name"))
    category_id: int | None = None
    if category_name:
        category = await shop_repo.get_category_by_name(category_name)
        if not category:
            try:
                category = await shop_repo.create_category(category_name)
            except Exception as exc:  # pragma: no cover - safety net for race conditions
                log_error(
                    "Failed to create shop category during import",
                    category_name=category_name,
                    error=str(exc),
                )
                category = await shop_repo.get_category_by_name(category_name)
        if category:
            category_id = int(category["id"])
    elif existing and existing.get("category_id") is not None:
        category_id = int(existing["category_id"])

    stock_at = _normalise_stock_date(feed_item.get("pub_date"))
    warranty_length = _clean_string(feed_item.get("warranty_length"))
    manufacturer = _clean_string(feed_item.get("manufacturer"))

    image_url = await _download_product_image(feed_item.get("image_url"), code, uploads_root)

    name = feed_name or code
    if existing:
        existing_name = _clean_string(existing.get("name"))
        existing_sku = _clean_string(existing.get("vendor_sku") or existing.get("sku"))
        if existing_name and existing_name.lower() != existing_sku.lower():
            name = existing_name

    await shop_repo.upsert_product_from_feed(
        name=name,
        sku=code,
        vendor_sku=code,
        description=description or "",
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

    log_info(
        "Imported shop product from stock feed",
        sku=code,
        vendor_sku=normalised_sku,
        image_refreshed=bool(image_url),
    )

    updated = await shop_repo.get_product_by_sku(code)
    return updated or {
        "sku": code,
        "vendor_sku": code,
        "name": name,
        "price": float(price),
        "vip_price": float(vip_price) if vip_price is not None else None,
        "stock": stock_total,
    }


async def _download_product_image(
    image_url: Any,
    sku: str,
    uploads_root: Path,
) -> str | None:
    if not image_url:
        return None

    url = str(image_url).strip()
    if not url.lower().startswith(("http://", "https://")):
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        log_error("Product image download failed", image_url=url, error=str(exc))
        return None

    if response.status_code != httpx.codes.OK:
        log_info(
            "Skipping product image download due to HTTP error",
            image_url=url,
            status=response.status_code,
        )
        return None

    data = response.content
    if len(data) > _MAX_IMAGE_BYTES:
        log_info(
            "Skipping product image that exceeds size limit",
            image_url=url,
            size=len(data),
        )
        return None

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    extension = _determine_extension(url, content_type)
    if extension not in _ALLOWED_IMAGE_EXTENSIONS:
        log_info(
            "Skipping product image with unsupported type",
            image_url=url,
            content_type=content_type,
        )
        return None

    safe_base = sanitize_filename(sku).rsplit(".", 1)[0] or "product"
    destination_dir = uploads_root / "shop"
    destination_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_base}{extension}"
    destination = destination_dir / filename

    try:
        async with aiofiles.open(destination, "wb") as buffer:
            await buffer.write(data)
    except Exception as exc:  # pragma: no cover - disk failures are rare
        log_error("Failed to persist product image", path=str(destination), error=str(exc))
        return None

    try:
        destination.chmod(0o600)
    except OSError:  # pragma: no cover - permission issues are platform specific
        log_info("Unable to adjust permissions on product image", path=str(destination))

    return f"/uploads/shop/{filename}"


def _determine_extension(url: str, content_type: str) -> str:
    parsed = re.search(r"\.([a-zA-Z0-9]+)(?:\?|$)", url)
    if parsed:
        ext = f".{parsed.group(1).lower()}"
        if ext in _ALLOWED_IMAGE_EXTENSIONS:
            return ext
    if content_type in _ALLOWED_IMAGE_MIME_MAP:
        return _ALLOWED_IMAGE_MIME_MAP[content_type]
    return ".jpg"


def _decimal_from_value(
    value: Any,
    *,
    fallback: Decimal | None = None,
    quantise: bool = True,
) -> Decimal:
    if value is None or value == "":
        if fallback is not None:
            return fallback
        return Decimal("0")
    if isinstance(value, Decimal):
        number = value
    else:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            if fallback is not None:
                return fallback
            return Decimal("0")
    if quantise:
        number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return number


def _normalise_stock_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    slash_match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", text)
    if slash_match:
        day, month, year = slash_match.groups()
        year = year if len(year) == 4 else f"20{year.zfill(2)}"
        return f"{year.zfill(4)}-{month.zfill(2)}-{day.zfill(2)}"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %Z")
            parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


__all__ = [
    "ProductImportError",
    "import_product_by_vendor_sku",
]
