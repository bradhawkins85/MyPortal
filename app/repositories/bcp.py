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


# ============================================================================
# Risk Management
# ============================================================================


async def list_risks(plan_id: int) -> list[dict[str, Any]]:
    """Get all risks for a plan."""
    query = """
        SELECT id, plan_id, description, likelihood, impact, rating, severity,
               preventative_actions, contingency_plans, created_at, updated_at
        FROM bcp_risk
        WHERE plan_id = %s
        ORDER BY rating DESC, id
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "description": row[2],
                    "likelihood": row[3],
                    "impact": row[4],
                    "rating": row[5],
                    "severity": row[6],
                    "preventative_actions": row[7],
                    "contingency_plans": row[8],
                    "created_at": row[9],
                    "updated_at": row[10],
                }
                for row in rows
            ]


async def get_risk_by_id(risk_id: int) -> dict[str, Any] | None:
    """Get a risk by ID."""
    query = """
        SELECT id, plan_id, description, likelihood, impact, rating, severity,
               preventative_actions, contingency_plans, created_at, updated_at
        FROM bcp_risk
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (risk_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "description": row[2],
                "likelihood": row[3],
                "impact": row[4],
                "rating": row[5],
                "severity": row[6],
                "preventative_actions": row[7],
                "contingency_plans": row[8],
                "created_at": row[9],
                "updated_at": row[10],
            }


async def create_risk(
    plan_id: int,
    description: str,
    likelihood: int,
    impact: int,
    rating: int,
    severity: str,
    preventative_actions: str | None = None,
    contingency_plans: str | None = None,
) -> dict[str, Any]:
    """Create a new risk."""
    query = """
        INSERT INTO bcp_risk 
        (plan_id, description, likelihood, impact, rating, severity, 
         preventative_actions, contingency_plans)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (plan_id, description, likelihood, impact, rating, severity,
                 preventative_actions, contingency_plans),
            )
            await conn.commit()
            risk_id = cursor.lastrowid
    
    return await get_risk_by_id(risk_id)


async def update_risk(
    risk_id: int,
    description: str | None = None,
    likelihood: int | None = None,
    impact: int | None = None,
    rating: int | None = None,
    severity: str | None = None,
    preventative_actions: str | None = None,
    contingency_plans: str | None = None,
) -> dict[str, Any] | None:
    """Update a risk."""
    updates = []
    values = []
    
    if description is not None:
        updates.append("description = %s")
        values.append(description)
    if likelihood is not None:
        updates.append("likelihood = %s")
        values.append(likelihood)
    if impact is not None:
        updates.append("impact = %s")
        values.append(impact)
    if rating is not None:
        updates.append("rating = %s")
        values.append(rating)
    if severity is not None:
        updates.append("severity = %s")
        values.append(severity)
    if preventative_actions is not None:
        updates.append("preventative_actions = %s")
        values.append(preventative_actions)
    if contingency_plans is not None:
        updates.append("contingency_plans = %s")
        values.append(contingency_plans)
    
    if not updates:
        return await get_risk_by_id(risk_id)
    
    values.append(risk_id)
    query = f"""
        UPDATE bcp_risk
        SET {', '.join(updates)}
        WHERE id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, values)
            await conn.commit()
    
    return await get_risk_by_id(risk_id)


async def delete_risk(risk_id: int) -> bool:
    """Delete a risk."""
    query = "DELETE FROM bcp_risk WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (risk_id,))
            await conn.commit()
            return cursor.rowcount > 0


async def get_risk_heatmap_data(plan_id: int) -> dict[str, Any]:
    """
    Get risk heatmap data showing count of risks in each cell.
    
    Returns a dictionary with:
    - cells: dict mapping (likelihood, impact) to count
    - total: total number of risks
    """
    query = """
        SELECT likelihood, impact, COUNT(*) as count
        FROM bcp_risk
        WHERE plan_id = %s AND likelihood IS NOT NULL AND impact IS NOT NULL
        GROUP BY likelihood, impact
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            
            cells = {}
            total = 0
            for row in rows:
                likelihood, impact, count = row[0], row[1], row[2]
                cells[f"{likelihood},{impact}"] = count
                total += count
            
            return {
                "cells": cells,
                "total": total
            }


async def seed_example_risks(plan_id: int) -> None:
    """Seed example risks for a new plan."""
    from app.services.risk_calculator import calculate_risk
    
    example_risks = [
        {
            "description": "Interruption to production processes (e.g., key equipment breakdown or fire)",
            "likelihood": 2,
            "impact": 4,
            "preventative_actions": "Maintain appropriate insurance coverage; establish 24-hour repair supplier contract; identify alternate production site",
            "contingency_plans": "Implement temporary workarounds; arrange bridging cash flow until insurance claim is paid; activate alternate site if needed"
        },
        {
            "description": "Burglary",
            "likelihood": 3,
            "impact": 3,
            "preventative_actions": "Insurance including theft coverage; install and maintain alarm system and CCTV; secure premises",
            "contingency_plans": "Contact insurance provider; maintain supplier list for fast replacement of stolen items; implement security review"
        }
    ]
    
    for risk_data in example_risks:
        rating, severity = calculate_risk(risk_data["likelihood"], risk_data["impact"])
        await create_risk(
            plan_id=plan_id,
            description=risk_data["description"],
            likelihood=risk_data["likelihood"],
            impact=risk_data["impact"],
            rating=rating,
            severity=severity,
            preventative_actions=risk_data["preventative_actions"],
            contingency_plans=risk_data["contingency_plans"]
        )


# ============================================================================
# Insurance Management
# ============================================================================


async def list_insurance_policies(plan_id: int) -> list[dict[str, Any]]:
    """Get all insurance policies for a plan."""
    query = """
        SELECT id, plan_id, type, coverage, exclusions, insurer, contact,
               last_review_date, payment_terms, created_at, updated_at
        FROM bcp_insurance_policy
        WHERE plan_id = %s
        ORDER BY type, id
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "type": row[2],
                    "coverage": row[3],
                    "exclusions": row[4],
                    "insurer": row[5],
                    "contact": row[6],
                    "last_review_date": row[7],
                    "payment_terms": row[8],
                    "created_at": row[9],
                    "updated_at": row[10],
                }
                for row in rows
            ]


async def get_insurance_policy_by_id(policy_id: int) -> dict[str, Any] | None:
    """Get an insurance policy by ID."""
    query = """
        SELECT id, plan_id, type, coverage, exclusions, insurer, contact,
               last_review_date, payment_terms, created_at, updated_at
        FROM bcp_insurance_policy
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (policy_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "type": row[2],
                "coverage": row[3],
                "exclusions": row[4],
                "insurer": row[5],
                "contact": row[6],
                "last_review_date": row[7],
                "payment_terms": row[8],
                "created_at": row[9],
                "updated_at": row[10],
            }


async def create_insurance_policy(
    plan_id: int,
    policy_type: str,
    coverage: str | None = None,
    exclusions: str | None = None,
    insurer: str | None = None,
    contact: str | None = None,
    last_review_date: datetime | None = None,
    payment_terms: str | None = None,
) -> dict[str, Any]:
    """Create a new insurance policy."""
    query = """
        INSERT INTO bcp_insurance_policy 
        (plan_id, type, coverage, exclusions, insurer, contact, 
         last_review_date, payment_terms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (plan_id, policy_type, coverage, exclusions, insurer, contact,
                 last_review_date, payment_terms),
            )
            await conn.commit()
            policy_id = cursor.lastrowid
    
    return await get_insurance_policy_by_id(policy_id)


async def update_insurance_policy(
    policy_id: int,
    policy_type: str | None = None,
    coverage: str | None = None,
    exclusions: str | None = None,
    insurer: str | None = None,
    contact: str | None = None,
    last_review_date: datetime | None = None,
    payment_terms: str | None = None,
) -> dict[str, Any] | None:
    """Update an insurance policy."""
    updates = []
    values = []
    
    if policy_type is not None:
        updates.append("type = %s")
        values.append(policy_type)
    if coverage is not None:
        updates.append("coverage = %s")
        values.append(coverage)
    if exclusions is not None:
        updates.append("exclusions = %s")
        values.append(exclusions)
    if insurer is not None:
        updates.append("insurer = %s")
        values.append(insurer)
    if contact is not None:
        updates.append("contact = %s")
        values.append(contact)
    if last_review_date is not None:
        updates.append("last_review_date = %s")
        values.append(last_review_date)
    if payment_terms is not None:
        updates.append("payment_terms = %s")
        values.append(payment_terms)
    
    if not updates:
        return await get_insurance_policy_by_id(policy_id)
    
    values.append(policy_id)
    query = f"""
        UPDATE bcp_insurance_policy
        SET {', '.join(updates)}
        WHERE id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, values)
            await conn.commit()
    
    return await get_insurance_policy_by_id(policy_id)


async def delete_insurance_policy(policy_id: int) -> bool:
    """Delete an insurance policy."""
    query = "DELETE FROM bcp_insurance_policy WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (policy_id,))
            await conn.commit()
            return cursor.rowcount > 0


# ============================================================================
# Backup Management
# ============================================================================


async def list_backup_items(plan_id: int) -> list[dict[str, Any]]:
    """Get all backup items for a plan."""
    query = """
        SELECT id, plan_id, data_scope, frequency, medium, owner, steps,
               created_at, updated_at
        FROM bcp_backup_item
        WHERE plan_id = %s
        ORDER BY data_scope, id
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "data_scope": row[2],
                    "frequency": row[3],
                    "medium": row[4],
                    "owner": row[5],
                    "steps": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                }
                for row in rows
            ]


async def get_backup_item_by_id(backup_id: int) -> dict[str, Any] | None:
    """Get a backup item by ID."""
    query = """
        SELECT id, plan_id, data_scope, frequency, medium, owner, steps,
               created_at, updated_at
        FROM bcp_backup_item
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (backup_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "data_scope": row[2],
                "frequency": row[3],
                "medium": row[4],
                "owner": row[5],
                "steps": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }


async def create_backup_item(
    plan_id: int,
    data_scope: str,
    frequency: str | None = None,
    medium: str | None = None,
    owner: str | None = None,
    steps: str | None = None,
) -> dict[str, Any]:
    """Create a new backup item."""
    query = """
        INSERT INTO bcp_backup_item 
        (plan_id, data_scope, frequency, medium, owner, steps)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (plan_id, data_scope, frequency, medium, owner, steps),
            )
            await conn.commit()
            backup_id = cursor.lastrowid
    
    return await get_backup_item_by_id(backup_id)


async def update_backup_item(
    backup_id: int,
    data_scope: str | None = None,
    frequency: str | None = None,
    medium: str | None = None,
    owner: str | None = None,
    steps: str | None = None,
) -> dict[str, Any] | None:
    """Update a backup item."""
    updates = []
    values = []
    
    if data_scope is not None:
        updates.append("data_scope = %s")
        values.append(data_scope)
    if frequency is not None:
        updates.append("frequency = %s")
        values.append(frequency)
    if medium is not None:
        updates.append("medium = %s")
        values.append(medium)
    if owner is not None:
        updates.append("owner = %s")
        values.append(owner)
    if steps is not None:
        updates.append("steps = %s")
        values.append(steps)
    
    if not updates:
        return await get_backup_item_by_id(backup_id)
    
    values.append(backup_id)
    query = f"""
        UPDATE bcp_backup_item
        SET {', '.join(updates)}
        WHERE id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, values)
            await conn.commit()
    
    return await get_backup_item_by_id(backup_id)


async def delete_backup_item(backup_id: int) -> bool:
    """Delete a backup item."""
    query = "DELETE FROM bcp_backup_item WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (backup_id,))
            await conn.commit()
            return cursor.rowcount > 0
