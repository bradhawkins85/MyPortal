"""
Repository layer for BC3 Business Continuity Planning system.

Provides database access for templates, plans, versions, reviews, attachments, and related entities.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.core.database import db


# ============================================================================
# BC Template Repository Functions
# ============================================================================

async def create_template(
    name: str,
    version: str,
    is_default: bool = False,
    schema_json: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a new BC template."""
    query = """
        INSERT INTO bc_template (name, version, is_default, schema_json)
        VALUES (%s, %s, %s, %s)
    """
    template_id = await db.execute(query, (name, version, is_default, schema_json))
    return await get_template_by_id(template_id)


async def get_template_by_id(template_id: int) -> dict[str, Any] | None:
    """Get a template by ID."""
    query = """
        SELECT id, name, version, is_default, schema_json, created_at, updated_at
        FROM bc_template
        WHERE id = %s
    """
    return await db.fetch_one(query, (template_id,))


async def list_templates(limit: int = 100) -> list[dict[str, Any]]:
    """List all templates."""
    query = """
        SELECT id, name, version, is_default, schema_json, created_at, updated_at
        FROM bc_template
        ORDER BY is_default DESC, created_at DESC
        LIMIT %s
    """
    return await db.fetch_all(query, (limit,))


async def get_default_template() -> dict[str, Any] | None:
    """Get the default template."""
    query = """
        SELECT id, name, version, is_default, schema_json, created_at, updated_at
        FROM bc_template
        WHERE is_default = TRUE
        LIMIT 1
    """
    return await db.fetch_one(query)


async def update_template(
    template_id: int,
    name: Optional[str] = None,
    version: Optional[str] = None,
    is_default: Optional[bool] = None,
    schema_json: Optional[dict[str, Any]] = None,
) -> dict[str, Any] | None:
    """Update a template."""
    updates = []
    params = []
    
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if version is not None:
        updates.append("version = %s")
        params.append(version)
    if is_default is not None:
        updates.append("is_default = %s")
        params.append(is_default)
    if schema_json is not None:
        updates.append("schema_json = %s")
        params.append(schema_json)
    
    if not updates:
        return await get_template_by_id(template_id)
    
    params.append(template_id)
    query = f"""
        UPDATE bc_template
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    await db.execute(query, tuple(params))
    return await get_template_by_id(template_id)


# ============================================================================
# BC Plan Repository Functions
# ============================================================================

async def create_plan(
    title: str,
    owner_user_id: int,
    status: str = "draft",
    org_id: Optional[int] = None,
    template_id: Optional[int] = None,
) -> dict[str, Any]:
    """Create a new BC plan."""
    query = """
        INSERT INTO bc_plan (org_id, title, status, template_id, owner_user_id)
        VALUES (%s, %s, %s, %s, %s)
    """
    plan_id = await db.execute(query, (org_id, title, status, template_id, owner_user_id))
    return await get_plan_by_id(plan_id)


async def get_plan_by_id(plan_id: int) -> dict[str, Any] | None:
    """Get a plan by ID."""
    query = """
        SELECT id, org_id, title, status, template_id, current_version_id,
               owner_user_id, approved_at_utc, created_at, updated_at
        FROM bc_plan
        WHERE id = %s
    """
    return await db.fetch_one(query, (plan_id,))


async def list_plans(
    status: Optional[str] = None,
    org_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    template_id: Optional[int] = None,
    search_query: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List plans with filtering."""
    conditions = []
    params = []
    
    if status:
        conditions.append("status = %s")
        params.append(status)
    if org_id is not None:
        conditions.append("org_id = %s")
        params.append(org_id)
    if owner_user_id is not None:
        conditions.append("owner_user_id = %s")
        params.append(owner_user_id)
    if template_id is not None:
        conditions.append("template_id = %s")
        params.append(template_id)
    if search_query:
        conditions.append("title LIKE %s")
        params.append(f"%{search_query}%")
    
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    params.extend([limit, offset])
    query = f"""
        SELECT id, org_id, title, status, template_id, current_version_id,
               owner_user_id, approved_at_utc, created_at, updated_at
        FROM bc_plan
        {where_clause}
        ORDER BY updated_at DESC
        LIMIT %s OFFSET %s
    """
    return await db.fetch_all(query, tuple(params))


async def count_plans(
    status: Optional[str] = None,
    org_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    template_id: Optional[int] = None,
    search_query: Optional[str] = None,
) -> int:
    """Count plans with filtering."""
    conditions = []
    params = []
    
    if status:
        conditions.append("status = %s")
        params.append(status)
    if org_id is not None:
        conditions.append("org_id = %s")
        params.append(org_id)
    if owner_user_id is not None:
        conditions.append("owner_user_id = %s")
        params.append(owner_user_id)
    if template_id is not None:
        conditions.append("template_id = %s")
        params.append(template_id)
    if search_query:
        conditions.append("title LIKE %s")
        params.append(f"%{search_query}%")
    
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    query = f"""
        SELECT COUNT(*) as count
        FROM bc_plan
        {where_clause}
    """
    result = await db.fetch_one(query, tuple(params))
    return result["count"] if result else 0


async def update_plan(
    plan_id: int,
    title: Optional[str] = None,
    status: Optional[str] = None,
    template_id: Optional[int] = None,
    current_version_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    approved_at_utc: Optional[datetime] = None,
) -> dict[str, Any] | None:
    """Update a plan."""
    updates = []
    params = []
    
    if title is not None:
        updates.append("title = %s")
        params.append(title)
    if status is not None:
        updates.append("status = %s")
        params.append(status)
    if template_id is not None:
        updates.append("template_id = %s")
        params.append(template_id)
    if current_version_id is not None:
        updates.append("current_version_id = %s")
        params.append(current_version_id)
    if owner_user_id is not None:
        updates.append("owner_user_id = %s")
        params.append(owner_user_id)
    if approved_at_utc is not None:
        updates.append("approved_at_utc = %s")
        params.append(approved_at_utc)
    
    if not updates:
        return await get_plan_by_id(plan_id)
    
    params.append(plan_id)
    query = f"""
        UPDATE bc_plan
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    await db.execute(query, tuple(params))
    return await get_plan_by_id(plan_id)


async def delete_plan(plan_id: int) -> None:
    """Delete a plan and all related data (cascade)."""
    query = "DELETE FROM bc_plan WHERE id = %s"
    await db.execute(query, (plan_id,))


# ============================================================================
# BC Plan Version Repository Functions
# ============================================================================

async def create_version(
    plan_id: int,
    version_number: int,
    authored_by_user_id: int,
    summary_change_note: Optional[str] = None,
    content_json: Optional[dict[str, Any]] = None,
    status: str = "active",
) -> dict[str, Any]:
    """Create a new plan version."""
    query = """
        INSERT INTO bc_plan_version 
        (plan_id, version_number, status, authored_by_user_id, summary_change_note, content_json)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    version_id = await db.execute(
        query,
        (plan_id, version_number, status, authored_by_user_id, summary_change_note, content_json)
    )
    return await get_version_by_id(version_id)


async def get_version_by_id(version_id: int) -> dict[str, Any] | None:
    """Get a version by ID."""
    query = """
        SELECT id, plan_id, version_number, status, authored_by_user_id,
               authored_at_utc, summary_change_note, content_json,
               docx_export_hash, pdf_export_hash
        FROM bc_plan_version
        WHERE id = %s
    """
    return await db.fetch_one(query, (version_id,))


async def list_plan_versions(plan_id: int) -> list[dict[str, Any]]:
    """List all versions for a plan."""
    query = """
        SELECT id, plan_id, version_number, status, authored_by_user_id,
               authored_at_utc, summary_change_note, content_json,
               docx_export_hash, pdf_export_hash
        FROM bc_plan_version
        WHERE plan_id = %s
        ORDER BY version_number DESC
    """
    return await db.fetch_all(query, (plan_id,))


async def get_active_version(plan_id: int) -> dict[str, Any] | None:
    """Get the active version for a plan."""
    query = """
        SELECT id, plan_id, version_number, status, authored_by_user_id,
               authored_at_utc, summary_change_note, content_json,
               docx_export_hash, pdf_export_hash
        FROM bc_plan_version
        WHERE plan_id = %s AND status = 'active'
        LIMIT 1
    """
    return await db.fetch_one(query, (plan_id,))


async def get_next_version_number(plan_id: int) -> int:
    """Get the next version number for a plan."""
    query = """
        SELECT MAX(version_number) as max_version
        FROM bc_plan_version
        WHERE plan_id = %s
    """
    result = await db.fetch_one(query, (plan_id,))
    if result and result["max_version"]:
        return result["max_version"] + 1
    return 1


async def activate_version(version_id: int, plan_id: int) -> dict[str, Any] | None:
    """Activate a version and supersede all others."""
    # First, supersede all other versions
    await db.execute(
        "UPDATE bc_plan_version SET status = 'superseded' WHERE plan_id = %s AND status = 'active'",
        (plan_id,)
    )
    
    # Then activate the specified version
    await db.execute(
        "UPDATE bc_plan_version SET status = 'active' WHERE id = %s",
        (version_id,)
    )
    
    # Update the plan's current_version_id
    await db.execute(
        "UPDATE bc_plan SET current_version_id = %s WHERE id = %s",
        (version_id, plan_id)
    )
    
    return await get_version_by_id(version_id)


async def update_version_export_hash(
    version_id: int,
    docx_hash: Optional[str] = None,
    pdf_hash: Optional[str] = None,
) -> dict[str, Any] | None:
    """Update export hashes for a version."""
    updates = []
    params = []
    
    if docx_hash is not None:
        updates.append("docx_export_hash = %s")
        params.append(docx_hash)
    if pdf_hash is not None:
        updates.append("pdf_export_hash = %s")
        params.append(pdf_hash)
    
    if not updates:
        return await get_version_by_id(version_id)
    
    params.append(version_id)
    query = f"""
        UPDATE bc_plan_version
        SET {', '.join(updates)}
        WHERE id = %s
    """
    await db.execute(query, tuple(params))
    return await get_version_by_id(version_id)


async def update_version_content(
    version_id: int,
    content_json: dict[str, Any],
) -> dict[str, Any] | None:
    """Update version content."""
    query = """
        UPDATE bc_plan_version
        SET content_json = %s
        WHERE id = %s
    """
    await db.execute(query, (content_json, version_id))
    return await get_version_by_id(version_id)


# ============================================================================
# BC Review Repository Functions
# ============================================================================

async def create_review(
    plan_id: int,
    requested_by_user_id: int,
    reviewer_user_id: int,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new review request."""
    query = """
        INSERT INTO bc_review (plan_id, requested_by_user_id, reviewer_user_id, status, notes)
        VALUES (%s, %s, %s, 'pending', %s)
    """
    review_id = await db.execute(query, (plan_id, requested_by_user_id, reviewer_user_id, notes))
    return await get_review_by_id(review_id)


async def get_review_by_id(review_id: int) -> dict[str, Any] | None:
    """Get a review by ID."""
    query = """
        SELECT id, plan_id, requested_by_user_id, reviewer_user_id, status,
               requested_at_utc, decided_at_utc, notes, created_at, updated_at
        FROM bc_review
        WHERE id = %s
    """
    return await db.fetch_one(query, (review_id,))


async def list_plan_reviews(plan_id: int) -> list[dict[str, Any]]:
    """List all reviews for a plan."""
    query = """
        SELECT id, plan_id, requested_by_user_id, reviewer_user_id, status,
               requested_at_utc, decided_at_utc, notes, created_at, updated_at
        FROM bc_review
        WHERE plan_id = %s
        ORDER BY requested_at_utc DESC
    """
    return await db.fetch_all(query, (plan_id,))


async def update_review_decision(
    review_id: int,
    status: str,
    notes: Optional[str] = None,
) -> dict[str, Any] | None:
    """Update a review with a decision."""
    query = """
        UPDATE bc_review
        SET status = %s, decided_at_utc = UTC_TIMESTAMP(), notes = %s
        WHERE id = %s
    """
    await db.execute(query, (status, notes, review_id))
    return await get_review_by_id(review_id)


# ============================================================================
# BC Acknowledgment Repository Functions
# ============================================================================

async def create_acknowledgment(
    plan_id: int,
    user_id: int,
    ack_version_number: Optional[int] = None,
) -> dict[str, Any]:
    """Create a plan acknowledgment."""
    query = """
        INSERT INTO bc_ack (plan_id, user_id, ack_version_number)
        VALUES (%s, %s, %s)
    """
    ack_id = await db.execute(query, (plan_id, user_id, ack_version_number))
    return await get_acknowledgment_by_id(ack_id)


async def get_acknowledgment_by_id(ack_id: int) -> dict[str, Any] | None:
    """Get an acknowledgment by ID."""
    query = """
        SELECT id, plan_id, user_id, ack_at_utc, ack_version_number
        FROM bc_ack
        WHERE id = %s
    """
    return await db.fetch_one(query, (ack_id,))


async def list_plan_acknowledgments(plan_id: int) -> list[dict[str, Any]]:
    """List all acknowledgments for a plan."""
    query = """
        SELECT id, plan_id, user_id, ack_at_utc, ack_version_number
        FROM bc_ack
        WHERE plan_id = %s
        ORDER BY ack_at_utc DESC
    """
    return await db.fetch_all(query, (plan_id,))


async def get_user_acknowledgment(plan_id: int, user_id: int) -> dict[str, Any] | None:
    """Get a user's latest acknowledgment for a plan."""
    query = """
        SELECT id, plan_id, user_id, ack_at_utc, ack_version_number
        FROM bc_ack
        WHERE plan_id = %s AND user_id = %s
        ORDER BY ack_at_utc DESC
        LIMIT 1
    """
    return await db.fetch_one(query, (plan_id, user_id))


async def get_users_pending_acknowledgment(
    plan_id: int,
    version_number: int,
) -> list[dict[str, Any]]:
    """
    Get users who have not acknowledged a specific version.
    
    Returns users with BC viewer permission or higher who have not acknowledged
    the specified version yet. Super admins are excluded from the list as they
    typically don't need to acknowledge plans.
    """
    query = """
        SELECT DISTINCT u.id, u.email, u.name
        FROM users u
        INNER JOIN company_memberships cm ON cm.user_id = u.id
        INNER JOIN permissions p ON (
            (p.company_id = cm.company_id AND p.user_id IS NULL) OR
            (p.user_id = u.id AND p.company_id IS NULL)
        )
        WHERE p.permission_key IN ('bc.viewer', 'bc.editor', 'bc.approver', 'bc.admin')
        AND u.is_super_admin = FALSE
        AND u.id NOT IN (
            -- Exclude users who already acknowledged this version or later
            SELECT user_id
            FROM bc_ack
            WHERE plan_id = %s
            AND (ack_version_number IS NULL OR ack_version_number >= %s)
        )
        ORDER BY u.name, u.email
    """
    return await db.fetch_all(query, (plan_id, version_number))


async def get_acknowledgment_summary(plan_id: int, version_number: int) -> dict[str, Any]:
    """
    Get acknowledgment summary for a plan version.
    
    Returns counts of total users with BC access, acknowledged users, and pending users.
    Super admins are excluded from counts.
    """
    # Get total users with BC access (excluding super admins)
    total_query = """
        SELECT COUNT(DISTINCT u.id) as total_users
        FROM users u
        INNER JOIN company_memberships cm ON cm.user_id = u.id
        INNER JOIN permissions p ON (
            (p.company_id = cm.company_id AND p.user_id IS NULL) OR
            (p.user_id = u.id AND p.company_id IS NULL)
        )
        WHERE p.permission_key IN ('bc.viewer', 'bc.editor', 'bc.approver', 'bc.admin')
        AND u.is_super_admin = FALSE
    """
    total_result = await db.fetch_one(total_query)
    total_users = total_result["total_users"] if total_result else 0
    
    # Get users who acknowledged this version or later
    ack_query = """
        SELECT COUNT(DISTINCT ba.user_id) as acknowledged_users
        FROM bc_ack ba
        INNER JOIN users u ON u.id = ba.user_id
        WHERE ba.plan_id = %s
        AND (ba.ack_version_number IS NULL OR ba.ack_version_number >= %s)
        AND u.is_super_admin = FALSE
    """
    ack_result = await db.fetch_one(ack_query, (plan_id, version_number))
    acknowledged_users = ack_result["acknowledged_users"] if ack_result else 0
    
    pending_users = total_users - acknowledged_users
    
    return {
        "total_users": total_users,
        "acknowledged_users": acknowledged_users,
        "pending_users": pending_users,
        "version_number": version_number,
    }


# ============================================================================
# BC Attachment Repository Functions
# ============================================================================

async def create_attachment(
    plan_id: int,
    file_name: str,
    storage_path: str,
    uploaded_by_user_id: int,
    content_type: Optional[str] = None,
    size_bytes: Optional[int] = None,
    file_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Create an attachment record."""
    query = """
        INSERT INTO bc_attachment 
        (plan_id, file_name, storage_path, content_type, size_bytes, uploaded_by_user_id, hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    attachment_id = await db.execute(
        query,
        (plan_id, file_name, storage_path, content_type, size_bytes, uploaded_by_user_id, file_hash)
    )
    return await get_attachment_by_id(attachment_id)


async def get_attachment_by_id(attachment_id: int) -> dict[str, Any] | None:
    """Get an attachment by ID."""
    query = """
        SELECT id, plan_id, file_name, storage_path, content_type, size_bytes,
               uploaded_by_user_id, uploaded_at_utc, hash, created_at, updated_at
        FROM bc_attachment
        WHERE id = %s
    """
    return await db.fetch_one(query, (attachment_id,))


async def list_plan_attachments(plan_id: int) -> list[dict[str, Any]]:
    """List all attachments for a plan."""
    query = """
        SELECT id, plan_id, file_name, storage_path, content_type, size_bytes,
               uploaded_by_user_id, uploaded_at_utc, hash, created_at, updated_at
        FROM bc_attachment
        WHERE plan_id = %s
        ORDER BY uploaded_at_utc DESC
    """
    return await db.fetch_all(query, (plan_id,))


async def delete_attachment(attachment_id: int) -> None:
    """Delete an attachment record."""
    query = "DELETE FROM bc_attachment WHERE id = %s"
    await db.execute(query, (attachment_id,))


# ============================================================================
# BC Audit Repository Functions
# ============================================================================

async def create_audit_entry(
    plan_id: int,
    action: str,
    actor_user_id: int,
    details_json: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create an audit trail entry."""
    query = """
        INSERT INTO bc_audit (plan_id, action, actor_user_id, details_json)
        VALUES (%s, %s, %s, %s)
    """
    audit_id = await db.execute(query, (plan_id, action, actor_user_id, details_json))
    return await get_audit_entry_by_id(audit_id)


async def get_audit_entry_by_id(audit_id: int) -> dict[str, Any] | None:
    """Get an audit entry by ID."""
    query = """
        SELECT id, plan_id, action, actor_user_id, details_json, at_utc
        FROM bc_audit
        WHERE id = %s
    """
    return await db.fetch_one(query, (audit_id,))


async def list_plan_audit_trail(plan_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """List audit trail for a plan."""
    query = """
        SELECT id, plan_id, action, actor_user_id, details_json, at_utc
        FROM bc_audit
        WHERE plan_id = %s
        ORDER BY at_utc DESC
        LIMIT %s
    """
    return await db.fetch_all(query, (plan_id, limit))


# ============================================================================
# BC Change Log Repository Functions
# ============================================================================

async def create_change_log_mapping(
    plan_id: int,
    change_guid: str,
) -> dict[str, Any]:
    """Create a change log mapping."""
    query = """
        INSERT INTO bc_change_log_map (plan_id, change_guid)
        VALUES (%s, %s)
    """
    mapping_id = await db.execute(query, (plan_id, change_guid))
    return await get_change_log_mapping_by_id(mapping_id)


async def get_change_log_mapping_by_id(mapping_id: int) -> dict[str, Any] | None:
    """Get a change log mapping by ID."""
    query = """
        SELECT id, plan_id, change_guid, imported_at_utc
        FROM bc_change_log_map
        WHERE id = %s
    """
    return await db.fetch_one(query, (mapping_id,))


async def list_plan_change_logs(plan_id: int) -> list[dict[str, Any]]:
    """List all change log mappings for a plan."""
    query = """
        SELECT id, plan_id, change_guid, imported_at_utc
        FROM bc_change_log_map
        WHERE plan_id = %s
        ORDER BY imported_at_utc DESC
    """
    return await db.fetch_all(query, (plan_id,))


# ============================================================================
# BC Risk Repository Functions
# ============================================================================

async def create_risk(
    plan_id: int,
    threat: str,
    likelihood: Optional[str] = None,
    impact: Optional[str] = None,
    rating: Optional[str] = None,
    mitigation: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Create a new risk assessment for a plan."""
    query = """
        INSERT INTO bc_risk (plan_id, threat, likelihood, impact, rating, mitigation, owner_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    risk_id = await db.execute(query, (plan_id, threat, likelihood, impact, rating, mitigation, owner_user_id))
    return await get_risk_by_id(risk_id)


async def get_risk_by_id(risk_id: int) -> dict[str, Any] | None:
    """Get a risk by ID."""
    query = """
        SELECT id, plan_id, threat, likelihood, impact, rating, mitigation, owner_user_id, created_at, updated_at
        FROM bc_risk
        WHERE id = %s
    """
    return await db.fetch_one(query, (risk_id,))


async def list_risks_by_plan(plan_id: int) -> list[dict[str, Any]]:
    """List all risks for a plan."""
    query = """
        SELECT id, plan_id, threat, likelihood, impact, rating, mitigation, owner_user_id, created_at, updated_at
        FROM bc_risk
        WHERE plan_id = %s
        ORDER BY rating DESC, created_at DESC
    """
    return await db.fetch_all(query, (plan_id,))


async def update_risk(
    risk_id: int,
    threat: Optional[str] = None,
    likelihood: Optional[str] = None,
    impact: Optional[str] = None,
    rating: Optional[str] = None,
    mitigation: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> dict[str, Any] | None:
    """Update a risk."""
    updates = []
    params = []
    
    if threat is not None:
        updates.append("threat = %s")
        params.append(threat)
    if likelihood is not None:
        updates.append("likelihood = %s")
        params.append(likelihood)
    if impact is not None:
        updates.append("impact = %s")
        params.append(impact)
    if rating is not None:
        updates.append("rating = %s")
        params.append(rating)
    if mitigation is not None:
        updates.append("mitigation = %s")
        params.append(mitigation)
    if owner_user_id is not None:
        updates.append("owner_user_id = %s")
        params.append(owner_user_id)
    
    if not updates:
        return await get_risk_by_id(risk_id)
    
    params.append(risk_id)
    query = f"""
        UPDATE bc_risk
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    await db.execute(query, tuple(params))
    return await get_risk_by_id(risk_id)


async def delete_risk(risk_id: int) -> None:
    """Delete a risk."""
    query = "DELETE FROM bc_risk WHERE id = %s"
    await db.execute(query, (risk_id,))
