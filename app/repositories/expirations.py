"""Metadata and live source queries for the expiration register.

Dates deliberately remain in their owning tables.  This module only stores
workflow metadata, so edits to an asset or article are visible immediately.
"""
from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_asset_dates(company_id: int | None) -> list[dict[str, Any]]:
    company_clause = "" if company_id is None else " WHERE a.company_id = %s"
    params = None if company_id is None else (company_id,)
    warranty_filter = " WHERE a.warranty_end_date IS NOT NULL"
    if company_clause:
        warranty_filter += " AND a.company_id = %s"
    warranties = await db.fetch_all(
        """SELECT a.id source_id, 'warranty' source_type,
                  'warranty_end_date' source_field, a.name title,
                  a.warranty_end_date due_at, a.company_id, c.name company_name
           FROM assets a JOIN companies c ON c.id = a.company_id"""
        + warranty_filter,
        params,
    )
    custom_clause = " AND a.company_id = %s" if company_id is not None else ""
    custom = await db.fetch_all(
        """SELECT a.id source_id, 'asset_custom_date' source_type,
                  d.name source_field, a.name title, v.value_date due_at,
                  a.company_id, c.name company_name,
                  COALESCE(d.display_name, d.name) detail
           FROM asset_custom_field_values v
           JOIN asset_custom_field_definitions d ON d.id = v.field_definition_id
           JOIN assets a ON a.id = v.asset_id
           JOIN companies c ON c.id = a.company_id
           WHERE d.field_type = 'date' AND v.value_date IS NOT NULL"""
        + custom_clause,
        params,
    )
    website_clause = "" if company_id is None else " AND w.company_id = %s"
    websites = await db.fetch_all(
        """SELECT w.id source_id, 'website' source_type,
                  CASE WHEN dates.kind = 'certificate' THEN 'certificate_expires_at' ELSE 'domain_expires_at' END source_field,
                  w.name title, CASE WHEN dates.kind = 'certificate' THEN w.certificate_expires_at ELSE w.domain_expires_at END due_at,
                  w.company_id, c.name company_name, dates.kind detail
           FROM websites w JOIN companies c ON c.id = w.company_id
           JOIN (SELECT 'certificate' kind UNION ALL SELECT 'domain') dates
           WHERE CASE WHEN dates.kind = 'certificate' THEN w.certificate_expires_at ELSE w.domain_expires_at END IS NOT NULL"""
        + website_clause,
        params,
    )
    return [dict(row) for row in warranties] + [dict(row) for row in custom] + [dict(row) for row in (websites or [])]


async def list_metadata(company_id: int | None) -> list[dict[str, Any]]:
    clause = "" if company_id is None else " WHERE m.company_id = %s"
    rows = await db.fetch_all(
        """SELECT m.*, u.first_name owner_first_name, u.last_name owner_last_name,
                  u.email owner_email
           FROM expiration_metadata m LEFT JOIN users u ON u.id = m.owner_user_id"""
        + clause,
        None if company_id is None else (company_id,),
    )
    result = []
    for row in rows:
        item = dict(row)
        full_name = " ".join(filter(None, (item.get("owner_first_name"), item.get("owner_last_name"))))
        item["owner_name"] = full_name or item.get("owner_email")
        result.append(item)
    return result


async def save_metadata(*, company_id: int, source_type: str, source_id: int,
                        source_field: str, lead_days: int, owner_user_id: int | None) -> None:
    await db.execute(
        """INSERT INTO expiration_metadata
           (company_id, source_type, source_id, source_field, lead_days, owner_user_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE lead_days = VALUES(lead_days),
             owner_user_id = VALUES(owner_user_id), updated_at = CURRENT_TIMESTAMP""",
        (company_id, source_type, source_id, source_field, lead_days, owner_user_id),
    )


async def record_attempt(*, company_id: int, source_type: str, source_id: int,
                         source_field: str, status: str, error: str | None = None) -> None:
    await db.execute(
        """INSERT INTO expiration_reminder_attempts
           (company_id, source_type, source_id, source_field, status, error_message)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (company_id, source_type, source_id, source_field, status, error[:500] if error else None),
    )
