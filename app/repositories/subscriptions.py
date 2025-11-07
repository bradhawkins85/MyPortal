"""Repository for managing customer subscriptions."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core.database import db


def _normalize_subscription(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a subscription row from the database."""
    return {
        "id": row["id"],
        "customer_id": int(row["customer_id"]),
        "product_id": int(row["product_id"]),
        "subscription_category_id": (
            int(row["subscription_category_id"])
            if row.get("subscription_category_id") is not None
            else None
        ),
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "quantity": int(row["quantity"]),
        "unit_price": Decimal(str(row["unit_price"])),
        "prorated_price": (
            Decimal(str(row["prorated_price"]))
            if row.get("prorated_price") is not None
            else None
        ),
        "status": row["status"],
        "auto_renew": bool(row["auto_renew"]),
        "created_at": row.get("created_at"),
        "created_by": (
            int(row["created_by"]) if row.get("created_by") is not None else None
        ),
        "updated_at": row.get("updated_at"),
    }


async def list_subscriptions(
    *,
    customer_id: int | None = None,
    product_id: int | None = None,
    category_id: int | None = None,
    status: str | None = None,
    end_before: date | None = None,
    end_after: date | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    """List subscriptions with optional filters."""
    conditions: list[str] = []
    params: list[Any] = []
    
    if customer_id is not None:
        conditions.append("customer_id = %s")
        params.append(customer_id)
    
    if product_id is not None:
        conditions.append("product_id = %s")
        params.append(product_id)
    
    if category_id is not None:
        conditions.append("subscription_category_id = %s")
        params.append(category_id)
    
    if status is not None:
        conditions.append("status = %s")
        params.append(status)
    
    if end_before is not None:
        conditions.append("end_date < %s")
        params.append(end_before)
    
    if end_after is not None:
        conditions.append("end_date > %s")
        params.append(end_after)
    
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    query = f"""
        SELECT s.*, p.name as product_name, c.name as category_name
        FROM subscriptions s
        LEFT JOIN shop_products p ON s.product_id = p.id
        LEFT JOIN subscription_categories c ON s.subscription_category_id = c.id
        {where_clause}
        ORDER BY s.end_date DESC, s.created_at DESC
    """
    
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    
    rows = await db.fetch_all(query, tuple(params))
    
    result: list[dict[str, Any]] = []
    for row in rows:
        sub = _normalize_subscription(row)
        sub["product_name"] = row.get("product_name")
        sub["category_name"] = row.get("category_name")
        result.append(sub)
    
    return result


async def get_subscription(subscription_id: str) -> dict[str, Any] | None:
    """Get a subscription by ID."""
    row = await db.fetch_one(
        """
        SELECT s.*, p.name as product_name, c.name as category_name
        FROM subscriptions s
        LEFT JOIN shop_products p ON s.product_id = p.id
        LEFT JOIN subscription_categories c ON s.subscription_category_id = c.id
        WHERE s.id = %s
        """,
        (subscription_id,),
    )
    if not row:
        return None
    
    sub = _normalize_subscription(row)
    sub["product_name"] = row.get("product_name")
    sub["category_name"] = row.get("category_name")
    return sub


async def create_subscription(
    *,
    customer_id: int,
    product_id: int,
    subscription_category_id: int | None,
    start_date: date,
    end_date: date,
    quantity: int,
    unit_price: Decimal,
    prorated_price: Decimal | None = None,
    status: str = "active",
    auto_renew: bool = True,
    created_by: int | None = None,
) -> dict[str, Any]:
    """Create a new subscription."""
    subscription_id = str(uuid4())
    
    await db.execute(
        """
        INSERT INTO subscriptions (
            id, customer_id, product_id, subscription_category_id,
            start_date, end_date, quantity, unit_price, prorated_price,
            status, auto_renew, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            subscription_id,
            customer_id,
            product_id,
            subscription_category_id,
            start_date,
            end_date,
            quantity,
            unit_price,
            prorated_price,
            status,
            auto_renew,
            created_by,
        ),
    )
    
    created = await get_subscription(subscription_id)
    if not created:
        raise RuntimeError(f"Failed to create subscription: {subscription_id}")
    return created


async def update_subscription(
    subscription_id: str,
    *,
    quantity: int | None = None,
    status: str | None = None,
    auto_renew: bool | None = None,
    end_date: date | None = None,
) -> None:
    """Update a subscription."""
    updates: list[str] = []
    params: list[Any] = []
    
    if quantity is not None:
        updates.append("quantity = %s")
        params.append(quantity)
    
    if status is not None:
        updates.append("status = %s")
        params.append(status)
    
    if auto_renew is not None:
        updates.append("auto_renew = %s")
        params.append(auto_renew)
    
    if end_date is not None:
        updates.append("end_date = %s")
        params.append(end_date)
    
    if not updates:
        return
    
    params.append(subscription_id)
    await db.execute(
        f"UPDATE subscriptions SET {', '.join(updates)} WHERE id = %s",
        tuple(params),
    )


async def get_category_anchor_end_date(
    customer_id: int, category_id: int
) -> date | None:
    """Get the maximum end_date for active subscriptions in a category for a customer.
    
    This is the anchor date used for co-terming new subscriptions.
    """
    row = await db.fetch_one(
        """
        SELECT MAX(end_date) as anchor_date
        FROM subscriptions
        WHERE customer_id = %s
          AND subscription_category_id = %s
          AND status IN ('active', 'pending_renewal')
        """,
        (customer_id, category_id),
    )
    
    if not row or row.get("anchor_date") is None:
        return None
    
    return row["anchor_date"]


async def cancel_subscription(subscription_id: str) -> None:
    """Cancel a subscription."""
    await update_subscription(subscription_id, status="canceled", auto_renew=False)


async def count_subscriptions(
    *,
    customer_id: int | None = None,
    status: str | None = None,
) -> int:
    """Count subscriptions with optional filters."""
    conditions: list[str] = []
    params: list[Any] = []
    
    if customer_id is not None:
        conditions.append("customer_id = %s")
        params.append(customer_id)
    
    if status is not None:
        conditions.append("status = %s")
        params.append(status)
    
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    row = await db.fetch_one(
        f"SELECT COUNT(*) as count FROM subscriptions {where_clause}",
        tuple(params),
    )
    
    return int(row["count"]) if row else 0
