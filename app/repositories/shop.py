from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

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
