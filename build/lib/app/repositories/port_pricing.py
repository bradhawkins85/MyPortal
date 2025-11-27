from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_VALID_STATUS = {"draft", "pending_review", "approved", "rejected"}


async def list_pricing_versions(
    port_id: int,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses = ["port_id = %s"]
    params: list[Any] = [port_id]
    if status and status in _VALID_STATUS:
        clauses.append("status = %s")
        params.append(status)
    sql = (
        "SELECT id, port_id, version_label, status, currency, base_rate, handling_rate, storage_rate, notes, "
        "submitted_by, approved_by, submitted_at, approved_at, rejection_reason, effective_from, effective_to, "
        "created_at, updated_at "
        f"FROM port_pricing_versions WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])
    rows = await db.fetch_all(sql, tuple(params))
    return list(rows)


async def get_pricing_version(pricing_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        """
        SELECT id, port_id, version_label, status, currency, base_rate, handling_rate, storage_rate, notes,
               submitted_by, approved_by, submitted_at, approved_at, rejection_reason, effective_from, effective_to,
               created_at, updated_at
        FROM port_pricing_versions
        WHERE id = %s
        """,
        (pricing_id,),
    )


async def create_pricing_version(**values: Any) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO port_pricing_versions (
            port_id, version_label, status, currency, base_rate, handling_rate, storage_rate, notes,
            submitted_by, approved_by, submitted_at, approved_at, rejection_reason, effective_from, effective_to
        )
        VALUES (
            %(port_id)s, %(version_label)s, %(status)s, %(currency)s, %(base_rate)s, %(handling_rate)s, %(storage_rate)s, %(notes)s,
            %(submitted_by)s, %(approved_by)s, %(submitted_at)s, %(approved_at)s, %(rejection_reason)s, %(effective_from)s, %(effective_to)s
        )
        """,
        values,
    )
    row = await db.fetch_one(
        """
        SELECT id, port_id, version_label, status, currency, base_rate, handling_rate, storage_rate, notes,
               submitted_by, approved_by, submitted_at, approved_at, rejection_reason, effective_from, effective_to,
               created_at, updated_at
        FROM port_pricing_versions
        WHERE port_id = %(port_id)s AND version_label = %(version_label)s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        values,
    )
    if not row:
        raise RuntimeError("Failed to persist pricing version")
    return row


async def update_pricing_version(pricing_id: int, **values: Any) -> dict[str, Any]:
    if not values:
        pricing = await get_pricing_version(pricing_id)
        if not pricing:
            raise ValueError("Pricing version not found")
        return pricing
    assignments = []
    params: list[Any] = []
    for column, value in values.items():
        assignments.append(f"{column} = %s")
        params.append(value)
    params.append(pricing_id)
    await db.execute(
        f"UPDATE port_pricing_versions SET {', '.join(assignments)} WHERE id = %s",
        tuple(params),
    )
    updated = await get_pricing_version(pricing_id)
    if not updated:
        raise ValueError("Pricing version not found after update")
    return updated


async def update_status(
    pricing_id: int,
    *,
    status: str,
    submitted_by: int | None = None,
    approved_by: int | None = None,
    rejection_reason: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    if status not in _VALID_STATUS:
        raise ValueError("Invalid pricing status")
    updates: dict[str, Any] = {"status": status, "rejection_reason": rejection_reason}
    if status == "pending_review":
        updates["submitted_by"] = submitted_by
        updates["submitted_at"] = timestamp or datetime.now(timezone.utc)
        updates["approved_by"] = None
        updates["approved_at"] = None
        updates["rejection_reason"] = None
    elif status == "approved":
        updates["approved_by"] = approved_by
        updates["approved_at"] = timestamp or datetime.now(timezone.utc)
        updates["rejection_reason"] = None
    elif status == "rejected":
        updates["approved_by"] = None
        updates["approved_at"] = None
        updates["submitted_by"] = submitted_by or updates.get("submitted_by")
        updates["rejection_reason"] = rejection_reason
    updated = await update_pricing_version(pricing_id, **updates)
    return updated
