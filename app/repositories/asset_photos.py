from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_for_asset(company_id: int, asset_id: int, *, customer_only: bool = False) -> list[dict[str, Any]]:
    condition = " AND customer_visible = TRUE" if customer_only else ""
    return list(await db.fetch_all(
        "SELECT id, asset_id, company_id, storage_name, thumbnail_name, content_type, "
        "size_bytes, caption, sort_order, customer_visible, uploaded_by, created_at "
        "FROM asset_photos WHERE company_id = %s AND asset_id = %s" + condition +
        " ORDER BY sort_order, id", (company_id, asset_id),
    ))


async def get(company_id: int, asset_id: int, photo_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM asset_photos WHERE id = %s AND asset_id = %s AND company_id = %s",
        (photo_id, asset_id, company_id),
    )


async def get_by_key(company_id: int, asset_id: int, key: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM asset_photos WHERE company_id = %s AND asset_id = %s AND idempotency_key = %s",
        (company_id, asset_id, key),
    )


async def create(**values: Any) -> dict[str, Any]:
    await db.execute(
        "INSERT INTO asset_photos (asset_id, company_id, storage_name, thumbnail_name, content_type, "
        "size_bytes, caption, sort_order, customer_visible, idempotency_key, uploaded_by) VALUES "
        "(%(asset_id)s, %(company_id)s, %(storage_name)s, %(thumbnail_name)s, %(content_type)s, "
        "%(size_bytes)s, %(caption)s, %(sort_order)s, %(customer_visible)s, %(idempotency_key)s, %(uploaded_by)s)", values,
    )
    row = await get_by_key(values["company_id"], values["asset_id"], values["idempotency_key"])
    if not row:
        raise RuntimeError("Failed to persist asset photo")
    return row


async def update(company_id: int, asset_id: int, photo_id: int, *, caption: str | None, sort_order: int, customer_visible: bool) -> None:
    await db.execute(
        "UPDATE asset_photos SET caption = %s, sort_order = %s, customer_visible = %s "
        "WHERE id = %s AND asset_id = %s AND company_id = %s",
        (caption, sort_order, customer_visible, photo_id, asset_id, company_id),
    )


async def delete(company_id: int, asset_id: int, photo_id: int) -> None:
    await db.execute("DELETE FROM asset_photos WHERE id = %s AND asset_id = %s AND company_id = %s", (photo_id, asset_id, company_id))
