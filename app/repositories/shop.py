from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import aiomysql

from app.core.database import db


@dataclass(slots=True)
class ProductFilters:
    include_archived: bool = False
    company_id: int | None = None
    category_id: int | None = None
    search_term: str | None = None


async def list_categories() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, name FROM shop_categories ORDER BY name"
    )
    return [
        {"id": int(row["id"]), "name": row["name"]}
        for row in rows
    ]


async def list_products(filters: ProductFilters) -> list[dict[str, Any]]:
    query_parts: list[str] = [
        "SELECT p.*, c.name AS category_name",
        "FROM shop_products AS p",
        "LEFT JOIN shop_categories AS c ON c.id = p.category_id",
    ]
    params: list[Any] = []

    if filters.company_id is not None:
        query_parts.append(
            "LEFT JOIN shop_product_exclusions AS e "
            "ON e.product_id = p.id AND e.company_id = %s"
        )
        params.append(filters.company_id)

    conditions: list[str] = []
    if not filters.include_archived:
        conditions.append("p.archived = 0")
    if filters.company_id is not None:
        conditions.append("e.product_id IS NULL")
    if filters.category_id is not None:
        conditions.append("p.category_id = %s")
        params.append(filters.category_id)
    if filters.search_term:
        like = f"%{filters.search_term}%"
        conditions.append(
            "(p.name LIKE %s OR p.sku LIKE %s OR p.vendor_sku LIKE %s)"
        )
        params.extend([like, like, like])

    if conditions:
        query_parts.append("WHERE " + " AND ".join(conditions))

    query_parts.append("ORDER BY p.name ASC")
    sql = " ".join(query_parts)

    rows = await db.fetch_all(sql, tuple(params) if params else None)
    return [_normalise_product(row) for row in rows]


async def list_all_products(include_archived: bool = False) -> list[dict[str, Any]]:
    filters = ProductFilters(include_archived=include_archived)
    return await list_products(filters)


async def list_product_restrictions() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT e.product_id, e.company_id, c.name AS company_name
        FROM shop_product_exclusions AS e
        INNER JOIN companies AS c ON c.id = e.company_id
        ORDER BY c.name
        """
    )
    restrictions: list[dict[str, Any]] = []
    for row in rows:
        restrictions.append(
            {
                "product_id": int(row["product_id"]),
                "company_id": int(row["company_id"]),
                "company_name": row["company_name"],
            }
        )
    return restrictions


async def get_category(category_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, name FROM shop_categories WHERE id = %s",
        (category_id,),
    )
    if not row:
        return None
    return {"id": int(row["id"]), "name": row["name"]}


async def get_product_by_id(product_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT p.*, c.name AS category_name
        FROM shop_products AS p
        LEFT JOIN shop_categories AS c ON c.id = p.category_id
        WHERE p.id = %s
        """,
        (product_id,),
    )
    return _normalise_product(row) if row else None


async def get_product_by_sku(
    sku: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any] | None:
    sql = [
        "SELECT p.*, c.name AS category_name",
        "FROM shop_products AS p",
        "LEFT JOIN shop_categories AS c ON c.id = p.category_id",
        "WHERE p.sku = %s",
    ]
    params: list[Any] = [sku]
    if not include_archived:
        sql.append("AND p.archived = 0")
    sql.append("LIMIT 1")
    row = await db.fetch_one(" ".join(sql), tuple(params))
    return _normalise_product(row) if row else None


async def create_product(
    *,
    name: str,
    sku: str,
    vendor_sku: str,
    price: Decimal,
    stock: int,
    description: str | None = None,
    vip_price: Decimal | None = None,
    category_id: int | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                INSERT INTO shop_products
                    (name, sku, vendor_sku, description, image_url, price, vip_price, stock, category_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    name,
                    sku,
                    vendor_sku,
                    description,
                    image_url,
                    price,
                    vip_price,
                    stock,
                    category_id,
                ),
            )
            product_id = int(cursor.lastrowid)
    product = await get_product_by_id(product_id)
    if not product:
        raise RuntimeError("Failed to create product")
    return product


async def delete_product(product_id: int) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "DELETE FROM shop_products WHERE id = %s",
                (product_id,),
            )
            return cursor.rowcount > 0


async def get_category_by_name(name: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, name FROM shop_categories WHERE name = %s",
        (name,),
    )
    if not row:
        return None
    return {"id": int(row["id"]), "name": row["name"]}


async def create_category(name: str) -> int:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "INSERT INTO shop_categories (name) VALUES (%s)",
                (name,),
            )
            category_id = int(cursor.lastrowid)
    return category_id


async def upsert_product_from_feed(
    *,
    name: str,
    sku: str,
    vendor_sku: str,
    description: str | None,
    image_url: str | None,
    price: Decimal,
    vip_price: Decimal,
    stock: int,
    category_id: int | None,
    stock_nsw: int,
    stock_qld: int,
    stock_vic: int,
    stock_sa: int,
    buy_price: Decimal | None,
    weight: Decimal | None,
    length: Decimal | None,
    width: Decimal | None,
    height: Decimal | None,
    stock_at: date | None,
    warranty_length: str | None,
    manufacturer: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO shop_products
            (name, sku, vendor_sku, description, image_url, price, vip_price, stock,
             category_id, stock_nsw, stock_qld, stock_vic, stock_sa, buy_price,
             weight, length, width, height, stock_at, warranty_length, manufacturer)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            sku = VALUES(sku),
            description = VALUES(description),
            image_url = IFNULL(VALUES(image_url), image_url),
            price = VALUES(price),
            vip_price = VALUES(vip_price),
            stock = VALUES(stock),
            category_id = VALUES(category_id),
            stock_nsw = VALUES(stock_nsw),
            stock_qld = VALUES(stock_qld),
            stock_vic = VALUES(stock_vic),
            stock_sa = VALUES(stock_sa),
            buy_price = VALUES(buy_price),
            weight = VALUES(weight),
            length = VALUES(length),
            width = VALUES(width),
            height = VALUES(height),
            stock_at = VALUES(stock_at),
            warranty_length = VALUES(warranty_length),
            manufacturer = VALUES(manufacturer)
        """,
        (
            name,
            sku,
            vendor_sku,
            description,
            image_url,
            price,
            vip_price,
            stock,
            category_id,
            stock_nsw,
            stock_qld,
            stock_vic,
            stock_sa,
            buy_price,
            weight,
            length,
            width,
            height,
            stock_at,
            warranty_length,
            manufacturer,
        ),
    )


def _normalise_product(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    normalised["id"] = _coerce_int(row.get("id"))
    normalised["category_id"] = _coerce_optional_int(row.get("category_id"))
    normalised["price"] = _coerce_decimal(row.get("price"), default=0.0)
    normalised["vip_price"] = _coerce_optional_decimal(row.get("vip_price"))
    normalised["buy_price"] = _coerce_optional_decimal(row.get("buy_price"))
    normalised["weight"] = _coerce_optional_decimal(row.get("weight"))
    normalised["length"] = _coerce_optional_decimal(row.get("length"))
    normalised["width"] = _coerce_optional_decimal(row.get("width"))
    normalised["height"] = _coerce_optional_decimal(row.get("height"))
    normalised["stock"] = _coerce_int(row.get("stock"), default=0)
    normalised["stock_nsw"] = _coerce_int(row.get("stock_nsw"), default=0)
    normalised["stock_qld"] = _coerce_int(row.get("stock_qld"), default=0)
    normalised["stock_vic"] = _coerce_int(row.get("stock_vic"), default=0)
    normalised["stock_sa"] = _coerce_int(row.get("stock_sa"), default=0)
    normalised["archived"] = bool(_coerce_int(row.get("archived"), default=0))
    stock_at = row.get("stock_at")
    if isinstance(stock_at, (datetime, date)):
        normalised["stock_at"] = stock_at.isoformat()
    elif stock_at is not None:
        normalised["stock_at"] = str(stock_at)
    return normalised


def _coerce_decimal(value: Any, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError("Decimal value is required")
        return default
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(Decimal(str(value)))


def _coerce_optional_decimal(value: Any) -> float | None:
    if value is None:
        return None
    return _coerce_decimal(value)


def _coerce_int(value: Any, *, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("Integer value is required")
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    return int(float(value))


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _coerce_int(value)
