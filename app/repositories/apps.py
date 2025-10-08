from typing import Any

from app.core.database import db


async def get_app_by_vendor_sku(vendor_sku: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM apps WHERE vendor_sku = %s OR sku = %s LIMIT 1",
        (vendor_sku, vendor_sku),
    )
    return dict(row) if row else None

