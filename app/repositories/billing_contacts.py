"""Repository for managing billing contacts."""
from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_billing_contacts_for_company(company_id: int) -> list[dict[str, Any]]:
    """Get all billing contacts for a company with user details."""
    rows = await db.fetch_all(
        """
        SELECT 
            bc.id,
            bc.company_id,
            bc.user_id,
            bc.created_at,
            u.email,
            u.first_name,
            u.last_name
        FROM billing_contacts bc
        JOIN users u ON u.id = bc.user_id
        WHERE bc.company_id = %s
        ORDER BY u.email
        """,
        (company_id,),
    )
    return [dict(row) for row in rows]


async def add_billing_contact(company_id: int, user_id: int) -> dict[str, Any]:
    """Add a user as a billing contact for a company."""
    await db.execute(
        """
        INSERT INTO billing_contacts (company_id, user_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE company_id = company_id
        """,
        (company_id, user_id),
    )
    
    row = await db.fetch_one(
        """
        SELECT 
            bc.id,
            bc.company_id,
            bc.user_id,
            bc.created_at,
            u.email,
            u.first_name,
            u.last_name
        FROM billing_contacts bc
        JOIN users u ON u.id = bc.user_id
        WHERE bc.company_id = %s AND bc.user_id = %s
        """,
        (company_id, user_id),
    )
    return dict(row) if row else {}


async def remove_billing_contact(company_id: int, user_id: int) -> None:
    """Remove a user as a billing contact for a company."""
    await db.execute(
        "DELETE FROM billing_contacts WHERE company_id = %s AND user_id = %s",
        (company_id, user_id),
    )


async def get_billing_contacts_for_companies(
    company_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Get billing contacts for multiple companies.
    
    Returns a dictionary mapping company_id to list of contact dictionaries.
    Each contact dictionary contains user_id and email.
    """
    if not company_ids:
        return {}
    
    placeholders = ", ".join(["%s"] * len(company_ids))
    rows = await db.fetch_all(
        f"""
        SELECT 
            bc.company_id,
            bc.user_id,
            u.email
        FROM billing_contacts bc
        JOIN users u ON u.id = bc.user_id
        WHERE bc.company_id IN ({placeholders})
        ORDER BY bc.company_id, u.email
        """,
        tuple(company_ids),
    )
    
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        company_id = int(row["company_id"])
        if company_id not in result:
            result[company_id] = []
        result[company_id].append({
            "user_id": int(row["user_id"]),
            "email": str(row["email"]),
        })
    
    return result
