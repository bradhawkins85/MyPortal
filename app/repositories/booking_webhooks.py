from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.database import db


async def get_webhook(webhook_id: int) -> dict[str, Any] | None:
    """Get a webhook by ID."""
    row = await db.fetch_one(
        "SELECT * FROM booking_webhooks WHERE id = %s",
        (webhook_id,)
    )
    if row:
        result = dict(row)
        if result.get("event_triggers") and isinstance(result["event_triggers"], str):
            result["event_triggers"] = json.loads(result["event_triggers"])
        return result
    return None


async def list_webhooks(
    user_id: int | None = None,
    active: bool | None = None,
    limit: int = 100
) -> list[dict[str, Any]]:
    """List webhooks with optional filters."""
    conditions = []
    params = []

    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)

    if active is not None:
        conditions.append("active = %s")
        params.append(1 if active else 0)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = await db.fetch_all(
        f"SELECT * FROM booking_webhooks {where_clause} ORDER BY id DESC LIMIT %s",
        tuple(params)
    )

    results = []
    for row in rows:
        result = dict(row)
        if result.get("event_triggers") and isinstance(result["event_triggers"], str):
            result["event_triggers"] = json.loads(result["event_triggers"])
        results.append(result)
    return results


async def create_webhook(
    user_id: int,
    subscriber_url: str,
    event_triggers: list[str],
    active: bool = True,
    secret_hash: str | None = None
) -> dict[str, Any]:
    """Create a new webhook."""
    event_triggers_json = json.dumps(event_triggers)

    await db.execute(
        """
        INSERT INTO booking_webhooks (user_id, subscriber_url, event_triggers, active, secret_hash)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, subscriber_url, event_triggers_json, 1 if active else 0, secret_hash)
    )

    row = await db.fetch_one(
        "SELECT * FROM booking_webhooks WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (user_id,)
    )

    if row:
        result = dict(row)
        if result.get("event_triggers") and isinstance(result["event_triggers"], str):
            result["event_triggers"] = json.loads(result["event_triggers"])
        return result
    return {}


async def update_webhook(webhook_id: int, **updates: Any) -> dict[str, Any]:
    """Update a webhook."""
    if not updates:
        webhook = await get_webhook(webhook_id)
        if not webhook:
            raise ValueError("Webhook not found")
        return webhook

    if "event_triggers" in updates and updates["event_triggers"] is not None:
        updates["event_triggers"] = json.dumps(updates["event_triggers"])

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [webhook_id]

    await db.execute(
        f"UPDATE booking_webhooks SET {columns} WHERE id = %s",
        tuple(params)
    )

    updated = await get_webhook(webhook_id)
    if not updated:
        raise ValueError("Webhook not found after update")
    return updated


async def delete_webhook(webhook_id: int) -> None:
    """Delete a webhook."""
    await db.execute(
        "DELETE FROM booking_webhooks WHERE id = %s",
        (webhook_id,)
    )


async def create_webhook_delivery(
    webhook_id: int,
    event_trigger: str,
    payload: dict[str, Any],
    booking_id: int | None = None
) -> dict[str, Any]:
    """Create a webhook delivery record."""
    payload_json = json.dumps(payload)

    await db.execute(
        """
        INSERT INTO booking_webhook_deliveries 
        (webhook_id, booking_id, event_trigger, payload)
        VALUES (%s, %s, %s, %s)
        """,
        (webhook_id, booking_id, event_trigger, payload_json)
    )

    row = await db.fetch_one(
        "SELECT * FROM booking_webhook_deliveries WHERE webhook_id = %s ORDER BY id DESC LIMIT 1",
        (webhook_id,)
    )

    if row:
        result = dict(row)
        if result.get("payload") and isinstance(result["payload"], str):
            result["payload"] = json.loads(result["payload"])
        return result
    return {}


async def update_webhook_delivery(
    delivery_id: int,
    response_status: int | None = None,
    response_body: str | None = None,
    delivered_at: datetime | None = None,
    failed_at: datetime | None = None,
    retry_count: int | None = None
) -> None:
    """Update a webhook delivery status."""
    updates = {}
    if response_status is not None:
        updates["response_status"] = response_status
    if response_body is not None:
        updates["response_body"] = response_body
    if delivered_at is not None:
        updates["delivered_at"] = delivered_at
    if failed_at is not None:
        updates["failed_at"] = failed_at
    if retry_count is not None:
        updates["retry_count"] = retry_count

    if updates:
        columns = ", ".join(f"{column} = %s" for column in updates.keys())
        params = list(updates.values()) + [delivery_id]
        await db.execute(
            f"UPDATE booking_webhook_deliveries SET {columns} WHERE id = %s",
            tuple(params)
        )


async def list_pending_deliveries(limit: int = 100) -> list[dict[str, Any]]:
    """List pending webhook deliveries for retry."""
    rows = await db.fetch_all(
        """
        SELECT * FROM booking_webhook_deliveries 
        WHERE delivered_at IS NULL 
        AND (failed_at IS NULL OR retry_count < 5)
        ORDER BY created_at 
        LIMIT %s
        """,
        (limit,)
    )

    results = []
    for row in rows:
        result = dict(row)
        if result.get("payload") and isinstance(result["payload"], str):
            result["payload"] = json.loads(result["payload"])
        results.append(result)
    return results
