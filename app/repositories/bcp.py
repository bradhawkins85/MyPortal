"""
Repository for BCP (Business Continuity Planning) operations.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


async def get_plan_by_company(company_id: int) -> dict[str, Any] | None:
    """Get BCP plan overview for a company."""
    query = """
        SELECT id, company_id, title, executive_summary, version, 
               last_reviewed, next_review, created_at, updated_at
        FROM bcp_plan_overview
        WHERE company_id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (company_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "company_id": row[1],
                "title": row[2],
                "executive_summary": row[3],
                "version": row[4],
                "last_reviewed": row[5],
                "next_review": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }


async def create_plan(
    company_id: int,
    title: str = "Business Continuity Plan",
    executive_summary: str | None = None,
    version: str = "1.0",
    last_reviewed: datetime | None = None,
    next_review: datetime | None = None,
) -> dict[str, Any]:
    """Create a new BCP plan overview."""
    query = """
        INSERT INTO bcp_plan_overview 
        (company_id, title, executive_summary, version, last_reviewed, next_review)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (company_id, title, executive_summary, version, last_reviewed, next_review),
            )
            await conn.commit()
            plan_id = cursor.lastrowid
            
    # Fetch and return the created plan
    return await get_plan_by_id(plan_id)


async def get_plan_by_id(plan_id: int) -> dict[str, Any] | None:
    """Get BCP plan by ID."""
    query = """
        SELECT id, company_id, title, executive_summary, version,
               last_reviewed, next_review, created_at, updated_at
        FROM bcp_plan_overview
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "company_id": row[1],
                "title": row[2],
                "executive_summary": row[3],
                "version": row[4],
                "last_reviewed": row[5],
                "next_review": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }


async def update_plan(
    plan_id: int,
    title: str | None = None,
    executive_summary: str | None = None,
    version: str | None = None,
    last_reviewed: datetime | None = None,
    next_review: datetime | None = None,
) -> dict[str, Any] | None:
    """Update BCP plan overview."""
    updates = []
    values = []
    
    if title is not None:
        updates.append("title = %s")
        values.append(title)
    if executive_summary is not None:
        updates.append("executive_summary = %s")
        values.append(executive_summary)
    if version is not None:
        updates.append("version = %s")
        values.append(version)
    if last_reviewed is not None:
        updates.append("last_reviewed = %s")
        values.append(last_reviewed)
    if next_review is not None:
        updates.append("next_review = %s")
        values.append(next_review)
    
    if not updates:
        return await get_plan_by_id(plan_id)
    
    values.append(plan_id)
    query = f"""
        UPDATE bcp_plan_overview
        SET {', '.join(updates)}
        WHERE id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, values)
            await conn.commit()
    
    return await get_plan_by_id(plan_id)


async def list_objectives(plan_id: int) -> list[dict[str, Any]]:
    """Get all objectives for a plan."""
    query = """
        SELECT id, plan_id, objective_text, display_order, created_at
        FROM bcp_objectives
        WHERE plan_id = %s
        ORDER BY display_order, id
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "objective_text": row[2],
                    "display_order": row[3],
                    "created_at": row[4],
                }
                for row in rows
            ]


async def create_objective(plan_id: int, objective_text: str, display_order: int = 0) -> dict[str, Any]:
    """Create a new objective for a plan."""
    query = """
        INSERT INTO bcp_objectives (plan_id, objective_text, display_order)
        VALUES (%s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id, objective_text, display_order))
            await conn.commit()
            objective_id = cursor.lastrowid
    
    # Return the created objective
    return await get_objective_by_id(objective_id)


async def get_objective_by_id(objective_id: int) -> dict[str, Any] | None:
    """Get objective by ID."""
    query = """
        SELECT id, plan_id, objective_text, display_order, created_at
        FROM bcp_objectives
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (objective_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "objective_text": row[2],
                "display_order": row[3],
                "created_at": row[4],
            }


async def delete_objective(objective_id: int) -> bool:
    """Delete an objective."""
    query = "DELETE FROM bcp_objectives WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (objective_id,))
            await conn.commit()
            return cursor.rowcount > 0


async def list_distribution_list(plan_id: int) -> list[dict[str, Any]]:
    """Get distribution list for a plan."""
    query = """
        SELECT id, plan_id, copy_number, name, location, created_at
        FROM bcp_distribution_list
        WHERE plan_id = %s
        ORDER BY copy_number
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "copy_number": row[2],
                    "name": row[3],
                    "location": row[4],
                    "created_at": row[5],
                }
                for row in rows
            ]


async def create_distribution_entry(
    plan_id: int, copy_number: str, name: str, location: str | None = None
) -> dict[str, Any]:
    """Create a new distribution list entry."""
    query = """
        INSERT INTO bcp_distribution_list (plan_id, copy_number, name, location)
        VALUES (%s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id, copy_number, name, location))
            await conn.commit()
            entry_id = cursor.lastrowid
    
    return await get_distribution_entry_by_id(entry_id)


async def get_distribution_entry_by_id(entry_id: int) -> dict[str, Any] | None:
    """Get distribution entry by ID."""
    query = """
        SELECT id, plan_id, copy_number, name, location, created_at
        FROM bcp_distribution_list
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (entry_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "copy_number": row[2],
                "name": row[3],
                "location": row[4],
                "created_at": row[5],
            }


async def delete_distribution_entry(entry_id: int) -> bool:
    """Delete a distribution list entry."""
    query = "DELETE FROM bcp_distribution_list WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (entry_id,))
            await conn.commit()
            return cursor.rowcount > 0


async def seed_default_objectives(plan_id: int) -> None:
    """Seed default objectives for a new plan."""
    default_objectives = [
        "Perform risk assessment",
        "Identify & prioritise critical activities",
        "Document immediate incident response",
        "Document recovery strategies/actions",
        "Review & update plan regularly",
    ]
    
    for index, objective in enumerate(default_objectives):
        await create_objective(plan_id, objective, display_order=index)
