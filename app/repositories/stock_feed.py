from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.core.database import db


# ---------------------------------------------------------------------------
# Price history helpers
# ---------------------------------------------------------------------------


async def record_price_if_changed(sku: str, dbp: Decimal | None) -> bool:
    """Record a DBP price entry for *sku* only when the price has changed.

    The initial price for a newly-seen SKU is always recorded.  Subsequent
    calls only insert a new row when *dbp* differs from the most recently
    recorded value.

    Returns ``True`` if a new row was written, ``False`` otherwise.
    """
    last_row = await db.fetch_one(
        "SELECT dbp FROM stock_feed_price_history"
        " WHERE sku = %s ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (sku,),
    )

    if last_row is not None:
        last_dbp: Decimal | None = (
            Decimal(str(last_row["dbp"])) if last_row["dbp"] is not None else None
        )
        if last_dbp == dbp:
            return False

    await db.execute(
        "INSERT INTO stock_feed_price_history (sku, dbp) VALUES (%s, %s)",
        (sku, dbp),
    )
    return True


async def get_price_history(sku: str) -> list[dict[str, Any]]:
    """Return all recorded DBP price entries for *sku* in ascending order."""
    rows = await db.fetch_all(
        "SELECT id, sku, dbp, recorded_at"
        " FROM stock_feed_price_history"
        " WHERE sku = %s ORDER BY recorded_at ASC, id ASC",
        (sku,),
    )
    return [
        {
            "id": row["id"],
            "sku": row["sku"],
            "dbp": Decimal(str(row["dbp"])) if row["dbp"] is not None else None,
            "recorded_at": row["recorded_at"],
        }
        for row in rows
    ]


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _coerce_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": row.get("sku"),
        "product_name": row.get("product_name", ""),
        "product_name2": row.get("product_name2") or None,
        "rrp": _coerce_decimal(row.get("rrp")),
        "category_name": row.get("category_name") or None,
        "on_hand_nsw": _coerce_int(row.get("on_hand_nsw")),
        "on_hand_qld": _coerce_int(row.get("on_hand_qld")),
        "on_hand_vic": _coerce_int(row.get("on_hand_vic")),
        "on_hand_sa": _coerce_int(row.get("on_hand_sa")),
        "dbp": _coerce_decimal(row.get("dbp")),
        "weight": _coerce_decimal(row.get("weight")),
        "length": _coerce_decimal(row.get("length")),
        "width": _coerce_decimal(row.get("width")),
        "height": _coerce_decimal(row.get("height")),
        "pub_date": row.get("pub_date") if row.get("pub_date") else None,
        "warranty_length": row.get("warranty_length") or None,
        "manufacturer": row.get("manufacturer") or None,
        "image_url": row.get("image_url") or None,
        "opt_accessori": row.get("opt_accessori") or None,
    }


async def get_item_by_sku(sku: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM stock_feed WHERE sku = %s",
        (sku,),
    )
    if not row:
        return None
    return _normalise_row(row)


async def list_all_items() -> list[dict[str, Any]]:
    """Return all items currently in the stock feed."""
    rows = await db.fetch_all("SELECT * FROM stock_feed ORDER BY sku")
    return [_normalise_row(row) for row in rows]


async def replace_feed(items: Sequence[Mapping[str, Any]]) -> None:
    """Replace the stock feed table contents with the provided items."""

    async with db.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM stock_feed")
                if items:
                    await cursor.executemany(
                        (
                            "INSERT INTO stock_feed ("
                            " sku, product_name, product_name2, rrp, category_name,"
                            " on_hand_nsw, on_hand_qld, on_hand_vic, on_hand_sa,"
                            " dbp, weight, length, width, height, pub_date,"
                            " warranty_length, manufacturer, image_url, opt_accessori"
                            ") VALUES ("
                            " %s, %s, %s, %s, %s,"
                            " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                            ")"
                        ),
                        [
                            (
                                item.get("sku"),
                                (item.get("product_name") or "").strip(),
                                item.get("product_name2"),
                                item.get("rrp"),
                                item.get("category_name"),
                                item.get("on_hand_nsw", 0),
                                item.get("on_hand_qld", 0),
                                item.get("on_hand_vic", 0),
                                item.get("on_hand_sa", 0),
                                item.get("dbp"),
                                item.get("weight"),
                                item.get("length"),
                                item.get("width"),
                                item.get("height"),
                                item.get("pub_date"),
                                item.get("warranty_length"),
                                item.get("manufacturer"),
                                item.get("image_url"),
                                item.get("opt_accessori"),
                            )
                            for item in items
                        ],
                    )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
