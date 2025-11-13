"""Repository for managing subscription change requests."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core.database import db


def _normalize_change_request(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a subscription change request row from the database."""
    return {
        "id": row["id"],
        "subscription_id": row["subscription_id"],
        "change_type": row["change_type"],
        "quantity_change": int(row["quantity_change"]),
        "requested_at": row["requested_at"],
        "requested_by": int(row["requested_by"]),
        "status": row["status"],
        "applied_at": row.get("applied_at"),
        "prorated_charge": (
            Decimal(str(row["prorated_charge"]))
            if row.get("prorated_charge") is not None
            else None
        ),
        "xero_invoice_number": row.get("xero_invoice_number"),
        "notes": row.get("notes"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def create_change_request(
    *,
    subscription_id: str,
    change_type: str,
    quantity_change: int,
    requested_by: int,
    prorated_charge: Decimal | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a new subscription change request."""
    change_request_id = str(uuid4())
    
    await db.execute(
        """
        INSERT INTO subscription_change_requests 
        (id, subscription_id, change_type, quantity_change, requested_by, 
         prorated_charge, notes, status, requested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
        """,
        (
            change_request_id,
            subscription_id,
            change_type,
            quantity_change,
            requested_by,
            prorated_charge,
            notes,
        ),
    )
    
    return await get_change_request(change_request_id)


async def get_change_request(change_request_id: str) -> dict[str, Any] | None:
    """Get a subscription change request by ID."""
    row = await db.fetch_one(
        "SELECT * FROM subscription_change_requests WHERE id = %s",
        (change_request_id,),
    )
    
    if not row:
        return None
    
    return _normalize_change_request(row)


async def list_pending_changes_for_subscription(
    subscription_id: str,
) -> list[dict[str, Any]]:
    """List all pending change requests for a subscription."""
    rows = await db.fetch_all(
        """
        SELECT * FROM subscription_change_requests 
        WHERE subscription_id = %s AND status = 'pending'
        ORDER BY requested_at ASC
        """,
        (subscription_id,),
    )
    
    return [_normalize_change_request(row) for row in rows]


async def list_pending_changes_for_subscriptions(
    subscription_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """List all pending change requests for multiple subscriptions.
    
    Returns a dict mapping subscription_id to list of pending changes.
    """
    if not subscription_ids:
        return {}
    
    placeholders = ", ".join(["%s"] * len(subscription_ids))
    rows = await db.fetch_all(
        f"""
        SELECT * FROM subscription_change_requests 
        WHERE subscription_id IN ({placeholders}) AND status = 'pending'
        ORDER BY subscription_id, requested_at ASC
        """,
        tuple(subscription_ids),
    )
    
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        subscription_id = row["subscription_id"]
        if subscription_id not in result:
            result[subscription_id] = []
        result[subscription_id].append(_normalize_change_request(row))
    
    return result


async def update_change_request_status(
    change_request_id: str,
    status: str,
) -> None:
    """Update the status of a change request."""
    applied_at = datetime.utcnow() if status == "applied" else None
    
    await db.execute(
        """
        UPDATE subscription_change_requests 
        SET status = %s, applied_at = %s
        WHERE id = %s
        """,
        (status, applied_at, change_request_id),
    )


async def cancel_change_request(change_request_id: str) -> None:
    """Cancel a pending change request."""
    await update_change_request_status(change_request_id, "cancelled")


async def apply_change_request(change_request_id: str) -> None:
    """Mark a change request as applied."""
    await update_change_request_status(change_request_id, "applied")


async def get_pending_decreases_by_end_date(
    end_date_before: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get all pending decrease requests for subscriptions ending before a given date.
    
    Used for processing decreases at the end of commitment terms.
    """
    if end_date_before:
        rows = await db.fetch_all(
            """
            SELECT scr.* 
            FROM subscription_change_requests scr
            JOIN subscriptions s ON scr.subscription_id = s.id
            WHERE scr.status = 'pending' 
              AND scr.change_type = 'decrease'
              AND s.end_date <= %s
            ORDER BY s.end_date ASC, scr.requested_at ASC
            """,
            (end_date_before,),
        )
    else:
        rows = await db.fetch_all(
            """
            SELECT scr.* 
            FROM subscription_change_requests scr
            WHERE scr.status = 'pending' 
              AND scr.change_type = 'decrease'
            ORDER BY scr.requested_at ASC
            """
        )
    
    return [_normalize_change_request(row) for row in rows]


async def update_xero_invoice_number(
    change_request_id: str,
    xero_invoice_number: str,
) -> None:
    """Update the Xero invoice number for a change request."""
    await db.execute(
        """
        UPDATE subscription_change_requests 
        SET xero_invoice_number = %s
        WHERE id = %s
        """,
        (xero_invoice_number, change_request_id),
    )
