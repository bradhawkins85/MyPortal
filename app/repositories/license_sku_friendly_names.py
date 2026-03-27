from __future__ import annotations

from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": str(row.get("sku") or "").strip(),
        "friendly_name": str(row.get("friendly_name") or "").strip(),
    }


async def list_mappings() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT sku, friendly_name
        FROM license_sku_friendly_names
        ORDER BY sku
        """
    )
    return [_normalise(row) for row in rows]


async def get_friendly_name(sku: str) -> str | None:
    row = await db.fetch_one(
        """
        SELECT friendly_name
        FROM license_sku_friendly_names
        WHERE sku = %s
        """,
        (sku,),
    )
    if not row:
        return None
    friendly_name = str(row.get("friendly_name") or "").strip()
    return friendly_name or None


async def upsert_mapping(sku: str, friendly_name: str) -> dict[str, Any]:
    cleaned_sku = sku.strip().upper()
    cleaned_name = friendly_name.strip()
    await db.execute(
        """
        INSERT INTO license_sku_friendly_names (sku, friendly_name)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE friendly_name = VALUES(friendly_name)
        """,
        (cleaned_sku, cleaned_name),
    )
    row = await db.fetch_one(
        """
        SELECT sku, friendly_name
        FROM license_sku_friendly_names
        WHERE sku = %s
        """,
        (cleaned_sku,),
    )
    if not row:
        raise RuntimeError("Failed to persist SKU mapping")
    return _normalise(row)


async def delete_mapping(sku: str) -> None:
    await db.execute(
        "DELETE FROM license_sku_friendly_names WHERE sku = %s",
        (sku.strip().upper(),),
    )
