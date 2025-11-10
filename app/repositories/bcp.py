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


# ============================================================================
# Business Impact Analysis (BIA) - Critical Activities
# ============================================================================


async def list_critical_activities(plan_id: int, sort_by: str = "importance") -> list[dict[str, Any]]:
    """
    Get all critical activities for a plan with their impact data.
    
    Args:
        plan_id: The BCP plan ID
        sort_by: Sort field - "importance", "priority", or "name"
    
    Returns:
        List of critical activities with nested impact data
    """
    # Determine sort order
    order_clause = "ORDER BY "
    if sort_by == "importance":
        order_clause += "ca.importance ASC NULLS LAST, ca.name"
    elif sort_by == "priority":
        order_clause += "FIELD(ca.priority, 'High', 'Medium', 'Low'), ca.name"
    else:  # name
        order_clause += "ca.name"
    
    query = f"""
        SELECT 
            ca.id, ca.plan_id, ca.name, ca.description, ca.priority, 
            ca.supplier_dependency, ca.importance, ca.notes,
            ca.created_at, ca.updated_at,
            i.id as impact_id, i.losses_financial, i.losses_increased_costs,
            i.losses_staffing, i.losses_product_service, i.losses_reputation,
            i.fines, i.legal_liability, i.rto_hours, i.losses_comments
        FROM bcp_critical_activity ca
        LEFT JOIN bcp_impact i ON i.critical_activity_id = ca.id
        WHERE ca.plan_id = %s
        {order_clause}
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            
            activities = []
            for row in rows:
                activity = {
                    "id": row[0],
                    "plan_id": row[1],
                    "name": row[2],
                    "description": row[3],
                    "priority": row[4],
                    "supplier_dependency": row[5],
                    "importance": row[6],
                    "notes": row[7],
                    "created_at": row[8],
                    "updated_at": row[9],
                    "impact": {
                        "id": row[10],
                        "losses_financial": row[11],
                        "losses_increased_costs": row[12],
                        "losses_staffing": row[13],
                        "losses_product_service": row[14],
                        "losses_reputation": row[15],
                        "fines": row[16],
                        "legal_liability": row[17],
                        "rto_hours": row[18],
                        "losses_comments": row[19],
                    } if row[10] else None
                }
                activities.append(activity)
            
            return activities


async def get_critical_activity_by_id(activity_id: int) -> dict[str, Any] | None:
    """Get a critical activity by ID with its impact data."""
    query = """
        SELECT 
            ca.id, ca.plan_id, ca.name, ca.description, ca.priority, 
            ca.supplier_dependency, ca.importance, ca.notes,
            ca.created_at, ca.updated_at,
            i.id as impact_id, i.losses_financial, i.losses_increased_costs,
            i.losses_staffing, i.losses_product_service, i.losses_reputation,
            i.fines, i.legal_liability, i.rto_hours, i.losses_comments
        FROM bcp_critical_activity ca
        LEFT JOIN bcp_impact i ON i.critical_activity_id = ca.id
        WHERE ca.id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (activity_id,))
            row = await cursor.fetchone()
            
            if not row:
                return None
            
            return {
                "id": row[0],
                "plan_id": row[1],
                "name": row[2],
                "description": row[3],
                "priority": row[4],
                "supplier_dependency": row[5],
                "importance": row[6],
                "notes": row[7],
                "created_at": row[8],
                "updated_at": row[9],
                "impact": {
                    "id": row[10],
                    "losses_financial": row[11],
                    "losses_increased_costs": row[12],
                    "losses_staffing": row[13],
                    "losses_product_service": row[14],
                    "losses_reputation": row[15],
                    "fines": row[16],
                    "legal_liability": row[17],
                    "rto_hours": row[18],
                    "losses_comments": row[19],
                } if row[10] else None
            }


async def create_critical_activity(
    plan_id: int,
    name: str,
    description: str | None = None,
    priority: str | None = None,
    supplier_dependency: str | None = None,
    importance: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a new critical activity."""
    query = """
        INSERT INTO bcp_critical_activity 
        (plan_id, name, description, priority, supplier_dependency, importance, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (plan_id, name, description, priority, supplier_dependency, importance, notes),
            )
            await conn.commit()
            activity_id = cursor.lastrowid
    
    return await get_critical_activity_by_id(activity_id)


async def update_critical_activity(
    activity_id: int,
    name: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    supplier_dependency: str | None = None,
    importance: int | None = None,
    notes: str | None = None,
) -> dict[str, Any] | None:
    """Update a critical activity."""
    updates = []
    values = []
    
    if name is not None:
        updates.append("name = %s")
        values.append(name)
    if description is not None:
        updates.append("description = %s")
        values.append(description)
    if priority is not None:
        updates.append("priority = %s")
        values.append(priority)
    if supplier_dependency is not None:
        updates.append("supplier_dependency = %s")
        values.append(supplier_dependency)
    if importance is not None:
        updates.append("importance = %s")
        values.append(importance)
    if notes is not None:
        updates.append("notes = %s")
        values.append(notes)
    
    if not updates:
        return await get_critical_activity_by_id(activity_id)
    
    values.append(activity_id)
    query = f"""
        UPDATE bcp_critical_activity
        SET {', '.join(updates)}
        WHERE id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, values)
            await conn.commit()
    
    return await get_critical_activity_by_id(activity_id)


async def delete_critical_activity(activity_id: int) -> bool:
    """Delete a critical activity (and its associated impact via CASCADE)."""
    query = "DELETE FROM bcp_critical_activity WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (activity_id,))
            await conn.commit()
            return cursor.rowcount > 0


# ============================================================================
# Business Impact Analysis (BIA) - Impact Data
# ============================================================================


async def create_or_update_impact(
    critical_activity_id: int,
    losses_financial: str | None = None,
    losses_increased_costs: str | None = None,
    losses_staffing: str | None = None,
    losses_product_service: str | None = None,
    losses_reputation: str | None = None,
    fines: str | None = None,
    legal_liability: str | None = None,
    rto_hours: int | None = None,
    losses_comments: str | None = None,
) -> dict[str, Any]:
    """
    Create or update impact data for a critical activity.
    If impact record exists, update it; otherwise create new.
    """
    # Check if impact record exists
    check_query = "SELECT id FROM bcp_impact WHERE critical_activity_id = %s"
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(check_query, (critical_activity_id,))
            existing = await cursor.fetchone()
            
            if existing:
                # Update existing record
                impact_id = existing[0]
                updates = []
                values = []
                
                if losses_financial is not None:
                    updates.append("losses_financial = %s")
                    values.append(losses_financial)
                if losses_increased_costs is not None:
                    updates.append("losses_increased_costs = %s")
                    values.append(losses_increased_costs)
                if losses_staffing is not None:
                    updates.append("losses_staffing = %s")
                    values.append(losses_staffing)
                if losses_product_service is not None:
                    updates.append("losses_product_service = %s")
                    values.append(losses_product_service)
                if losses_reputation is not None:
                    updates.append("losses_reputation = %s")
                    values.append(losses_reputation)
                if fines is not None:
                    updates.append("fines = %s")
                    values.append(fines)
                if legal_liability is not None:
                    updates.append("legal_liability = %s")
                    values.append(legal_liability)
                if rto_hours is not None:
                    updates.append("rto_hours = %s")
                    values.append(rto_hours)
                if losses_comments is not None:
                    updates.append("losses_comments = %s")
                    values.append(losses_comments)
                
                if updates:
                    values.append(impact_id)
                    update_query = f"""
                        UPDATE bcp_impact
                        SET {', '.join(updates)}
                        WHERE id = %s
                    """
                    await cursor.execute(update_query, values)
                    await conn.commit()
            else:
                # Create new record
                insert_query = """
                    INSERT INTO bcp_impact
                    (critical_activity_id, losses_financial, losses_increased_costs,
                     losses_staffing, losses_product_service, losses_reputation,
                     fines, legal_liability, rto_hours, losses_comments)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                await cursor.execute(
                    insert_query,
                    (critical_activity_id, losses_financial, losses_increased_costs,
                     losses_staffing, losses_product_service, losses_reputation,
                     fines, legal_liability, rto_hours, losses_comments),
                )
                await conn.commit()
                impact_id = cursor.lastrowid
    
    # Return the updated activity with impact
    return await get_critical_activity_by_id(critical_activity_id)


# ============================================================================
# Incident Management
# ============================================================================


async def list_incidents(plan_id: int) -> list[dict[str, Any]]:
    """Get all incidents for a plan."""
    query = """
        SELECT id, plan_id, started_at, status, source, created_at, updated_at
        FROM bcp_incident
        WHERE plan_id = %s
        ORDER BY started_at DESC
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "started_at": row[2],
                    "status": row[3],
                    "source": row[4],
                    "created_at": row[5],
                    "updated_at": row[6],
                }
                for row in rows
            ]


async def get_incident_by_id(incident_id: int) -> dict[str, Any] | None:
    """Get an incident by ID."""
    query = """
        SELECT id, plan_id, started_at, status, source, created_at, updated_at
        FROM bcp_incident
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (incident_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "started_at": row[2],
                "status": row[3],
                "source": row[4],
                "created_at": row[5],
                "updated_at": row[6],
            }


async def get_active_incident(plan_id: int) -> dict[str, Any] | None:
    """Get the active incident for a plan, if any."""
    query = """
        SELECT id, plan_id, started_at, status, source, created_at, updated_at
        FROM bcp_incident
        WHERE plan_id = %s AND status = 'Active'
        ORDER BY started_at DESC
        LIMIT 1
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "started_at": row[2],
                "status": row[3],
                "source": row[4],
                "created_at": row[5],
                "updated_at": row[6],
            }


async def create_incident(
    plan_id: int,
    started_at: datetime,
    source: str = "Manual",
) -> dict[str, Any]:
    """Create a new incident."""
    query = """
        INSERT INTO bcp_incident (plan_id, started_at, status, source)
        VALUES (%s, %s, 'Active', %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id, started_at, source))
            await conn.commit()
            incident_id = cursor.lastrowid
    
    return await get_incident_by_id(incident_id)


async def close_incident(incident_id: int) -> dict[str, Any] | None:
    """Close an incident."""
    query = """
        UPDATE bcp_incident
        SET status = 'Closed'
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (incident_id,))
            await conn.commit()
    
    return await get_incident_by_id(incident_id)


# ============================================================================
# Checklist Management
# ============================================================================


async def list_checklist_items(plan_id: int, phase: str = None) -> list[dict[str, Any]]:
    """Get all checklist items for a plan, optionally filtered by phase."""
    if phase:
        query = """
            SELECT id, plan_id, phase, label, default_order, created_at, updated_at
            FROM bcp_checklist_item
            WHERE plan_id = %s AND phase = %s
            ORDER BY default_order, id
        """
        params = (plan_id, phase)
    else:
        query = """
            SELECT id, plan_id, phase, label, default_order, created_at, updated_at
            FROM bcp_checklist_item
            WHERE plan_id = %s
            ORDER BY phase, default_order, id
        """
        params = (plan_id,)
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "phase": row[2],
                    "label": row[3],
                    "default_order": row[4],
                    "created_at": row[5],
                    "updated_at": row[6],
                }
                for row in rows
            ]


async def create_checklist_item(
    plan_id: int,
    phase: str,
    label: str,
    default_order: int = 0,
) -> dict[str, Any]:
    """Create a new checklist item."""
    query = """
        INSERT INTO bcp_checklist_item (plan_id, phase, label, default_order)
        VALUES (%s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id, phase, label, default_order))
            await conn.commit()
            item_id = cursor.lastrowid
    
    return await get_checklist_item_by_id(item_id)


async def get_checklist_item_by_id(item_id: int) -> dict[str, Any] | None:
    """Get a checklist item by ID."""
    query = """
        SELECT id, plan_id, phase, label, default_order, created_at, updated_at
        FROM bcp_checklist_item
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (item_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "phase": row[2],
                "label": row[3],
                "default_order": row[4],
                "created_at": row[5],
                "updated_at": row[6],
            }


async def seed_default_checklist_items(plan_id: int) -> None:
    """Seed default immediate response checklist items."""
    default_items = [
        "Assess incident severity",
        "Evacuate if required",
        "Account for all personnel",
        "Identify injuries",
        "Contact emergency services",
        "Implement incident response plan",
        "Start event log",
        "Activate staff/resources",
        "Appoint spokesperson",
        "Prioritise information gathering",
        "Brief team on incident",
        "Allocate roles/responsibilities",
        "Identify damage",
        "Identify disrupted critical activities",
        "Keep staff informed",
        "Contact key stakeholders",
        "Ensure regulatory/compliance requirements are met",
        "Initiate media/PR response",
    ]
    
    for index, label in enumerate(default_items):
        await create_checklist_item(plan_id, "Immediate", label, default_order=index)


async def get_checklist_ticks_for_incident(incident_id: int) -> list[dict[str, Any]]:
    """Get all checklist ticks for an incident."""
    query = """
        SELECT ct.id, ct.plan_id, ct.checklist_item_id, ct.incident_id,
               ct.is_done, ct.done_at, ct.done_by, ct.created_at, ct.updated_at,
               ci.label, ci.phase, ci.default_order
        FROM bcp_checklist_tick ct
        JOIN bcp_checklist_item ci ON ci.id = ct.checklist_item_id
        WHERE ct.incident_id = %s
        ORDER BY ci.default_order, ci.id
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (incident_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "checklist_item_id": row[2],
                    "incident_id": row[3],
                    "is_done": row[4],
                    "done_at": row[5],
                    "done_by": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                    "label": row[9],
                    "phase": row[10],
                    "default_order": row[11],
                }
                for row in rows
            ]


async def initialize_checklist_ticks(plan_id: int, incident_id: int) -> None:
    """Initialize checklist ticks for a new incident."""
    # Get all immediate response checklist items
    items = await list_checklist_items(plan_id, phase="Immediate")
    
    # Create a tick for each item
    query = """
        INSERT INTO bcp_checklist_tick 
        (plan_id, checklist_item_id, incident_id, is_done)
        VALUES (%s, %s, %s, FALSE)
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            for item in items:
                await cursor.execute(query, (plan_id, item["id"], incident_id))
            await conn.commit()


async def toggle_checklist_tick(
    tick_id: int,
    is_done: bool,
    done_by: int,
    done_at: datetime,
) -> dict[str, Any] | None:
    """Toggle a checklist tick."""
    query = """
        UPDATE bcp_checklist_tick
        SET is_done = %s, done_at = %s, done_by = %s
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (is_done, done_at if is_done else None, done_by if is_done else None, tick_id))
            await conn.commit()
    
    return await get_checklist_tick_by_id(tick_id)


async def get_checklist_tick_by_id(tick_id: int) -> dict[str, Any] | None:
    """Get a checklist tick by ID."""
    query = """
        SELECT ct.id, ct.plan_id, ct.checklist_item_id, ct.incident_id,
               ct.is_done, ct.done_at, ct.done_by, ct.created_at, ct.updated_at,
               ci.label, ci.phase, ci.default_order
        FROM bcp_checklist_tick ct
        JOIN bcp_checklist_item ci ON ci.id = ct.checklist_item_id
        WHERE ct.id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (tick_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "checklist_item_id": row[2],
                "incident_id": row[3],
                "is_done": row[4],
                "done_at": row[5],
                "done_by": row[6],
                "created_at": row[7],
                "updated_at": row[8],
                "label": row[9],
                "phase": row[10],
                "default_order": row[11],
            }


# ============================================================================
# Contacts Management
# ============================================================================


async def list_contacts(plan_id: int, kind: str = None) -> list[dict[str, Any]]:
    """Get all contacts for a plan, optionally filtered by kind."""
    if kind:
        query = """
            SELECT id, plan_id, kind, person_or_org, phones, email, 
                   responsibility_or_agency, created_at, updated_at
            FROM bcp_contact
            WHERE plan_id = %s AND kind = %s
            ORDER BY person_or_org
        """
        params = (plan_id, kind)
    else:
        query = """
            SELECT id, plan_id, kind, person_or_org, phones, email, 
                   responsibility_or_agency, created_at, updated_at
            FROM bcp_contact
            WHERE plan_id = %s
            ORDER BY kind, person_or_org
        """
        params = (plan_id,)
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "kind": row[2],
                    "person_or_org": row[3],
                    "phones": row[4],
                    "email": row[5],
                    "responsibility_or_agency": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                }
                for row in rows
            ]


async def create_contact(
    plan_id: int,
    kind: str,
    person_or_org: str,
    phones: str | None = None,
    email: str | None = None,
    responsibility_or_agency: str | None = None,
) -> dict[str, Any]:
    """Create a new contact."""
    query = """
        INSERT INTO bcp_contact 
        (plan_id, kind, person_or_org, phones, email, responsibility_or_agency)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (plan_id, kind, person_or_org, phones, email, responsibility_or_agency),
            )
            await conn.commit()
            contact_id = cursor.lastrowid
    
    return await get_contact_by_id(contact_id)


async def get_contact_by_id(contact_id: int) -> dict[str, Any] | None:
    """Get a contact by ID."""
    query = """
        SELECT id, plan_id, kind, person_or_org, phones, email, 
               responsibility_or_agency, created_at, updated_at
        FROM bcp_contact
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (contact_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "kind": row[2],
                "person_or_org": row[3],
                "phones": row[4],
                "email": row[5],
                "responsibility_or_agency": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }


async def update_contact(
    contact_id: int,
    kind: str | None = None,
    person_or_org: str | None = None,
    phones: str | None = None,
    email: str | None = None,
    responsibility_or_agency: str | None = None,
) -> dict[str, Any] | None:
    """Update a contact."""
    updates = []
    values = []
    
    if kind is not None:
        updates.append("kind = %s")
        values.append(kind)
    if person_or_org is not None:
        updates.append("person_or_org = %s")
        values.append(person_or_org)
    if phones is not None:
        updates.append("phones = %s")
        values.append(phones)
    if email is not None:
        updates.append("email = %s")
        values.append(email)
    if responsibility_or_agency is not None:
        updates.append("responsibility_or_agency = %s")
        values.append(responsibility_or_agency)
    
    if not updates:
        return await get_contact_by_id(contact_id)
    
    values.append(contact_id)
    query = f"""
        UPDATE bcp_contact
        SET {', '.join(updates)}
        WHERE id = %s
    """
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, values)
            await conn.commit()
    
    return await get_contact_by_id(contact_id)


async def delete_contact(contact_id: int) -> bool:
    """Delete a contact."""
    query = "DELETE FROM bcp_contact WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (contact_id,))
            await conn.commit()
            return cursor.rowcount > 0


# ============================================================================
# Event Log Management
# ============================================================================


async def list_event_log_entries(
    plan_id: int,
    incident_id: int | None = None,
) -> list[dict[str, Any]]:
    """Get event log entries for a plan, optionally filtered by incident."""
    if incident_id:
        query = """
            SELECT id, plan_id, incident_id, happened_at, author_id, notes, initials,
                   created_at, updated_at
            FROM bcp_event_log_entry
            WHERE plan_id = %s AND incident_id = %s
            ORDER BY happened_at DESC
        """
        params = (plan_id, incident_id)
    else:
        query = """
            SELECT id, plan_id, incident_id, happened_at, author_id, notes, initials,
                   created_at, updated_at
            FROM bcp_event_log_entry
            WHERE plan_id = %s
            ORDER BY happened_at DESC
        """
        params = (plan_id,)
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "incident_id": row[2],
                    "happened_at": row[3],
                    "author_id": row[4],
                    "notes": row[5],
                    "initials": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                }
                for row in rows
            ]


async def create_event_log_entry(
    plan_id: int,
    incident_id: int | None,
    happened_at: datetime,
    notes: str,
    author_id: int | None = None,
    initials: str | None = None,
) -> dict[str, Any]:
    """Create a new event log entry."""
    query = """
        INSERT INTO bcp_event_log_entry 
        (plan_id, incident_id, happened_at, author_id, notes, initials)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (plan_id, incident_id, happened_at, author_id, notes, initials),
            )
            await conn.commit()
            entry_id = cursor.lastrowid
    
    return await get_event_log_entry_by_id(entry_id)


async def get_event_log_entry_by_id(entry_id: int) -> dict[str, Any] | None:
    """Get an event log entry by ID."""
    query = """
        SELECT id, plan_id, incident_id, happened_at, author_id, notes, initials,
               created_at, updated_at
        FROM bcp_event_log_entry
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
                "incident_id": row[2],
                "happened_at": row[3],
                "author_id": row[4],
                "notes": row[5],
                "initials": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }


# ============================================================================
# BCP Roles and Assignments
# ============================================================================


async def list_roles(plan_id: int) -> list[dict[str, Any]]:
    """List all roles for a plan."""
    query = """
        SELECT id, plan_id, title, responsibilities, created_at, updated_at
        FROM bcp_role
        WHERE plan_id = %s
        ORDER BY title
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "title": row[2],
                    "responsibilities": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                }
                for row in rows
            ]


async def get_role_by_id(role_id: int) -> dict[str, Any] | None:
    """Get a role by ID."""
    query = """
        SELECT id, plan_id, title, responsibilities, created_at, updated_at
        FROM bcp_role
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (role_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "plan_id": row[1],
                "title": row[2],
                "responsibilities": row[3],
                "created_at": row[4],
                "updated_at": row[5],
            }


async def create_role(
    plan_id: int,
    title: str,
    responsibilities: str | None = None,
) -> dict[str, Any]:
    """Create a new role."""
    query = """
        INSERT INTO bcp_role (plan_id, title, responsibilities)
        VALUES (%s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (plan_id, title, responsibilities))
            await conn.commit()
            role_id = cursor.lastrowid
    
    return await get_role_by_id(role_id)


async def update_role(
    role_id: int,
    title: str | None = None,
    responsibilities: str | None = None,
) -> dict[str, Any] | None:
    """Update a role."""
    updates = []
    params = []
    
    if title is not None:
        updates.append("title = %s")
        params.append(title)
    
    if responsibilities is not None:
        updates.append("responsibilities = %s")
        params.append(responsibilities)
    
    if not updates:
        return await get_role_by_id(role_id)
    
    params.append(role_id)
    query = f"UPDATE bcp_role SET {', '.join(updates)} WHERE id = %s"
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, tuple(params))
            await conn.commit()
    
    return await get_role_by_id(role_id)


async def delete_role(role_id: int) -> bool:
    """Delete a role."""
    query = "DELETE FROM bcp_role WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (role_id,))
            await conn.commit()
            return cursor.rowcount > 0


async def list_role_assignments(role_id: int) -> list[dict[str, Any]]:
    """List all assignments for a role."""
    query = """
        SELECT id, role_id, user_id, is_alternate, contact_info, created_at, updated_at
        FROM bcp_role_assignment
        WHERE role_id = %s
        ORDER BY is_alternate, id
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (role_id,))
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "role_id": row[1],
                    "user_id": row[2],
                    "is_alternate": bool(row[3]),
                    "contact_info": row[4],
                    "created_at": row[5],
                    "updated_at": row[6],
                }
                for row in rows
            ]


async def get_role_assignment_by_id(assignment_id: int) -> dict[str, Any] | None:
    """Get a role assignment by ID."""
    query = """
        SELECT id, role_id, user_id, is_alternate, contact_info, created_at, updated_at
        FROM bcp_role_assignment
        WHERE id = %s
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (assignment_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "role_id": row[1],
                "user_id": row[2],
                "is_alternate": bool(row[3]),
                "contact_info": row[4],
                "created_at": row[5],
                "updated_at": row[6],
            }


async def create_role_assignment(
    role_id: int,
    user_id: int,
    is_alternate: bool = False,
    contact_info: str | None = None,
) -> dict[str, Any]:
    """Create a new role assignment."""
    query = """
        INSERT INTO bcp_role_assignment (role_id, user_id, is_alternate, contact_info)
        VALUES (%s, %s, %s, %s)
    """
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (role_id, user_id, is_alternate, contact_info))
            await conn.commit()
            assignment_id = cursor.lastrowid
    
    return await get_role_assignment_by_id(assignment_id)


async def update_role_assignment(
    assignment_id: int,
    user_id: int | None = None,
    is_alternate: bool | None = None,
    contact_info: str | None = None,
) -> dict[str, Any] | None:
    """Update a role assignment."""
    updates = []
    params = []
    
    if user_id is not None:
        updates.append("user_id = %s")
        params.append(user_id)
    
    if is_alternate is not None:
        updates.append("is_alternate = %s")
        params.append(is_alternate)
    
    if contact_info is not None:
        updates.append("contact_info = %s")
        params.append(contact_info)
    
    if not updates:
        return await get_role_assignment_by_id(assignment_id)
    
    params.append(assignment_id)
    query = f"UPDATE bcp_role_assignment SET {', '.join(updates)} WHERE id = %s"
    
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, tuple(params))
            await conn.commit()
    
    return await get_role_assignment_by_id(assignment_id)


async def delete_role_assignment(assignment_id: int) -> bool:
    """Delete a role assignment."""
    query = "DELETE FROM bcp_role_assignment WHERE id = %s"
    async with db.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (assignment_id,))
            await conn.commit()
            return cursor.rowcount > 0


async def list_roles_with_assignments(plan_id: int) -> list[dict[str, Any]]:
    """List all roles with their assignments for a plan."""
    roles = await list_roles(plan_id)
    
    # Fetch all assignments for these roles
    for role in roles:
        assignments = await list_role_assignments(role["id"])
        role["assignments"] = assignments
    
    return roles


async def seed_example_team_leader_role(plan_id: int) -> dict[str, Any]:
    """Seed an example Team Leader role with responsibilities."""
    responsibilities = """• Activate the business continuity plan
• Oversee response and recovery operations
• Decide on alternate site activation if needed
• Communicate with key stakeholders
• Brief the communications team on incident status
• Keep key staff apprised of situation and actions"""
    
    return await create_role(plan_id, "Team Leader", responsibilities)
