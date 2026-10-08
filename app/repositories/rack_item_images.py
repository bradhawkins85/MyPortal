"""Company-scoped rack item image library and per-item attachments.

The library is keyed by item type so a photo of one device can be reused across
other devices of the same type; attachments (``rack_equipment_images``) decide which
of those images a specific rack item actually displays.
"""
from __future__ import annotations

from typing import Any

from app.core.database import db
from app.services import rack_item_types

IMAGE_URL = "/api/infrastructure/rack-item-images/{image_id}"
THUMB_URL = "/api/infrastructure/rack-item-images/{image_id}/thumb"
_IMAGE_SELECT = (
    "SELECT i.id, i.kind, i.caption, i.content_type, i.size_bytes, "
    "i.source_product_id, i.created_at"
)


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "caption": row.get("caption") or "",
        "content_type": row.get("content_type"),
        "size_bytes": row.get("size_bytes"),
        "source_product_id": row.get("source_product_id"),
        "created_at": str(row["created_at"]) if row.get("created_at") is not None else None,
        "url": IMAGE_URL.format(image_id=row["id"]),
        "thumb_url": THUMB_URL.format(image_id=row["id"]),
    }


async def create_image(
    company_id: int, item_type: str, kind: str, *, storage_name: str, thumbnail_name: str | None,
    content_type: str, size_bytes: int, content_hash: str, caption: str | None = None,
    source_product_id: int | None = None, uploaded_by: int | None = None,
) -> tuple[int, bool]:
    """Insert a library image, or return the existing identical image (deduped by hash)."""
    item_type = rack_item_types.normalise(item_type)
    if kind not in {"device", "product"}:
        raise ValueError("Invalid image kind")
    if not content_hash:
        raise ValueError("Image content hash is required")
    existing = await db.fetch_one(
        "SELECT id FROM rack_item_images WHERE company_id=%s AND item_type=%s AND kind=%s AND content_hash=%s",
        (company_id, item_type, kind, content_hash))
    if existing:
        return int(existing["id"]), False
    image_id = await db.execute_returning_lastrowid(
        "INSERT INTO rack_item_images "
        "(company_id, item_type, kind, storage_name, thumbnail_name, content_type, size_bytes, "
        "content_hash, caption, source_product_id, uploaded_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (company_id, item_type, kind, storage_name, thumbnail_name, content_type, size_bytes,
         content_hash, (caption or "").strip()[:191] or None, source_product_id, uploaded_by))
    return int(image_id), True


async def get_image(company_id: int, image_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM rack_item_images WHERE id=%s AND company_id=%s", (image_id, company_id))
    return row


async def find_shop_image(company_id: int, item_type: str, kind: str,
                          product_id: int) -> dict[str, Any] | None:
    """Find an imported shop image, preferring this company's library entry.

    Callers must check access to the shop product first. Only the stored file
    metadata is reused across companies; library entries remain company-scoped.
    """
    return await db.fetch_one(
        "SELECT id, company_id, item_type, storage_name, thumbnail_name, content_type, "
        "size_bytes, content_hash FROM rack_item_images "
        "WHERE source_product_id=%s AND kind=%s "
        "ORDER BY (company_id=%s AND item_type=%s) DESC, id LIMIT 1",
        (product_id, kind, company_id, rack_item_types.normalise(item_type)))


async def storage_is_referenced(storage_name: str) -> bool:
    """Keep shared originals and thumbnails until their last library entry is removed."""
    return bool(await db.fetch_one(
        "SELECT id FROM rack_item_images WHERE storage_name=%s LIMIT 1", (storage_name,)))


async def delete_image(company_id: int, image_id: int) -> dict[str, Any] | None:
    """Delete a library image (attachments cascade) and return the row so files can be removed."""
    row = await get_image(company_id, image_id)
    if not row:
        return None
    await db.execute("DELETE FROM rack_item_images WHERE id=%s AND company_id=%s", (image_id, company_id))
    return row


async def list_library(company_id: int, item_type: str, kind: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Return the gallery for an item type: ``{'device': [...], 'product': [...]}``."""
    item_type = rack_item_types.normalise(item_type)
    query = _IMAGE_SELECT + " FROM rack_item_images i WHERE i.company_id=%s AND i.item_type=%s"
    params: list[Any] = [company_id, item_type]
    if kind in {"device", "product"}:
        query += " AND i.kind=%s"
        params.append(kind)
    query += " ORDER BY i.kind, i.created_at DESC"
    rows = list(await db.fetch_all(query, tuple(params)) or [])
    grouped: dict[str, list[dict[str, Any]]] = {"device": [], "product": []}
    for row in rows:
        grouped[row["kind"]].append(_shape(row))
    return grouped


async def attach(company_id: int, equipment_id: int, image_id: int) -> None:
    """Attach a library image to a rack item (idempotent, type-checked)."""
    item = await db.fetch_one(
        "SELECT id, item_type FROM rack_equipment WHERE id=%s AND company_id=%s",
        (equipment_id, company_id))
    if not item:
        raise ValueError("Rack item not found")
    image = await db.fetch_one(
        "SELECT id, item_type FROM rack_item_images WHERE id=%s AND company_id=%s",
        (image_id, company_id))
    if not image:
        raise ValueError("Image not found")
    if image["item_type"] != item["item_type"]:
        raise ValueError("Image type does not match this rack item")
    await db.execute(
        "INSERT IGNORE INTO rack_equipment_images (company_id, equipment_id, image_id) VALUES (%s, %s, %s)",
        (company_id, equipment_id, image_id))


async def detach(company_id: int, equipment_id: int, image_id: int) -> None:
    """Detach an image from a rack item without touching the shared library."""
    await db.execute(
        "DELETE FROM rack_equipment_images WHERE company_id=%s AND equipment_id=%s AND image_id=%s",
        (company_id, equipment_id, image_id))


async def list_equipment_images(company_id: int, equipment_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return this item's attached images grouped by kind."""
    rows = list(await db.fetch_all(
        _IMAGE_SELECT + " FROM rack_item_images i JOIN rack_equipment_images a ON a.image_id=i.id "
        "WHERE a.company_id=%s AND a.equipment_id=%s ORDER BY i.kind, i.created_at",
        (company_id, equipment_id)) or [])
    grouped: dict[str, list[dict[str, Any]]] = {"device": [], "product": []}
    for row in rows:
        grouped[row["kind"]].append(_shape(row))
    return grouped


async def equipment_image_map(company_id: int) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """Map each equipment id to its attached images (for the overview page)."""
    rows = list(await db.fetch_all(
        "SELECT a.equipment_id, i.id, i.kind, i.caption, i.content_type, i.size_bytes, i.created_at "
        "FROM rack_equipment_images a JOIN rack_item_images i ON i.id=a.image_id "
        "WHERE a.company_id=%s ORDER BY a.equipment_id, i.kind, i.created_at",
        (company_id,)) or [])
    result: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        grouped = result.setdefault(int(row["equipment_id"]), {"device": [], "product": []})
        grouped[row["kind"]].append(_shape(row))
    return result
