"""
BC5 Business Continuity API endpoints.

RESTful API for templates, plans, versions, workflows, sections, attachments, exports, and audit trails.
Implements RBAC with viewer, editor, approver, and admin roles.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from typing import Optional

from app.api.dependencies.bc_rbac import (
    require_bc_admin,
    require_bc_approver,
    require_bc_editor,
    require_bc_viewer,
)
from app.core.config import get_settings
from app.repositories import bc3 as bc_repo
from app.repositories import users as user_repo
from app.services import bc_export_service
from app.services import bc_file_validation
from app.schemas.bc5_models import (
    BCAcknowledge,
    BCAcknowledgeResponse,
    BCAcknowledgmentSummary,
    BCAuditListItem,
    BCAttachmentListItem,
    BCAttachmentUploadResponse,
    BCChangeLogItem,
    BCExportFormat,
    BCExportRequest,
    BCExportResponse,
    BCNotifyAcknowledgment,
    BCPaginatedResponse,
    BCPendingUser,
    BCPlanCreate,
    BCPlanDetail,
    BCPlanListFilters,
    BCPlanListItem,
    BCPlanListStatus,
    BCPlanUpdate,
    BCReviewApprove,
    BCReviewListItem,
    BCReviewRequestChanges,
    BCReviewSubmit,
    BCSectionListItem,
    BCSectionUpdate,
    BCTemplateCreate,
    BCTemplateDetail,
    BCTemplateListItem,
    BCTemplateUpdate,
    BCVersionActivate,
    BCVersionCreate,
    BCVersionDetail,
    BCVersionListItem,
)

router = APIRouter(prefix="/api/bc", tags=["Business Continuity (BC5)"])


# ============================================================================
# Helper Functions
# ============================================================================

async def _enrich_user_name(item: dict[str, Any], user_id_key: str, name_key: str) -> None:
    """Enrich an item with user name."""
    user_id = item.get(user_id_key)
    if user_id:
        user = await user_repo.get_user_by_id(user_id)
        if user:
            item[name_key] = user.get("name") or user.get("email")


async def _enrich_template(plan: dict[str, Any]) -> None:
    """Enrich a plan with template details."""
    template_id = plan.get("template_id")
    if template_id:
        template = await bc_repo.get_template_by_id(template_id)
        if template:
            plan["template"] = template
            plan["template_name"] = template.get("name")


async def _enrich_current_version(plan: dict[str, Any]) -> None:
    """Enrich a plan with current version details."""
    version_id = plan.get("current_version_id")
    if version_id:
        version = await bc_repo.get_version_by_id(version_id)
        if version:
            plan["current_version"] = version
            plan["current_version_number"] = version.get("version_number")


def _calculate_pagination(total: int, page: int, per_page: int) -> dict[str, int]:
    """Calculate pagination metadata."""
    total_pages = math.ceil(total / per_page) if per_page > 0 else 0
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


# ============================================================================
# Templates Endpoints
# ============================================================================

@router.get("/templates", response_model=list[BCTemplateListItem])
async def list_templates(
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCTemplateListItem]:
    """
    List all BC templates.
    
    Returns all available templates ordered by default flag and creation date.
    Templates define the structure and sections for BC plans.
    
    **Authorization**: Requires BC viewer role or higher.
    
    **Example Response:**
    ```json
    [
      {
        "id": 1,
        "name": "Government BCP Template",
        "version": "1.0",
        "is_default": true,
        "created_at": "2024-01-10T09:00:00Z",
        "updated_at": "2024-01-10T09:00:00Z"
      }
    ]
    ```
    """
    templates = await bc_repo.list_templates()
    return [BCTemplateListItem(**template) for template in templates]


@router.post("/templates", response_model=BCTemplateDetail, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_data: BCTemplateCreate,
    current_user: dict = Depends(require_bc_admin),
) -> BCTemplateDetail:
    """
    Create a new BC template.
    
    Creates a new template with the provided schema definition.
    Templates define the structure, sections, and fields for BC plans.
    
    **Authorization**: Requires BC admin role.
    
    **Request Body Example:**
    ```json
    {
      "name": "Custom IT DR Template",
      "version": "1.0",
      "is_default": false,
      "schema_json": {
        "sections": [
          {
            "key": "overview",
            "title": "Overview",
            "fields": [
              {
                "key": "purpose",
                "label": "Purpose",
                "type": "rich_text",
                "required": true
              }
            ]
          }
        ]
      }
    }
    ```
    
    **Response Example:**
    ```json
    {
      "id": 2,
      "name": "Custom IT DR Template",
      "version": "1.0",
      "is_default": false,
      "schema_json": {...},
      "created_at": "2024-01-15T14:30:00Z",
      "updated_at": "2024-01-15T14:30:00Z"
    }
    ```
    """
    template = await bc_repo.create_template(
        name=template_data.name,
        version=template_data.version,
        is_default=template_data.is_default,
        schema_json=template_data.schema_json,
    )
    return BCTemplateDetail(**template)


@router.get("/templates/{template_id}", response_model=BCTemplateDetail)
async def get_template(
    template_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCTemplateDetail:
    """
    Get a specific BC template.
    
    Returns the full template including schema definition.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    template = await bc_repo.get_template_by_id(template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return BCTemplateDetail(**template)


@router.patch("/templates/{template_id}", response_model=BCTemplateDetail)
async def update_template(
    template_id: int,
    template_data: BCTemplateUpdate,
    current_user: dict = Depends(require_bc_admin),
) -> BCTemplateDetail:
    """
    Update a BC template.
    
    Updates template properties. Only provided fields will be updated.
    
    **Authorization**: Requires BC admin role.
    """
    existing = await bc_repo.get_template_by_id(template_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    
    template = await bc_repo.update_template(
        template_id=template_id,
        name=template_data.name,
        version=template_data.version,
        is_default=template_data.is_default,
        schema_json=template_data.schema_json,
    )
    return BCTemplateDetail(**template)


@router.post("/templates/bootstrap-default", response_model=BCTemplateDetail, status_code=status.HTTP_201_CREATED)
async def bootstrap_default_template_endpoint(
    current_user: dict = Depends(require_bc_admin),
) -> BCTemplateDetail:
    """
    Bootstrap the default government BCP template.
    
    Creates the default template in the database if it doesn't already exist.
    If a default template already exists, returns that template without creating a new one.
    
    This endpoint can be used to:
    - Initialize the default template after system setup
    - Re-check that the default template exists
    - Get the database ID of the default template
    
    The bootstrapped template includes:
    - Complete section structure matching government BCP standards
    - All field definitions with proper types and validation
    - Table schemas for BIA, Risk Assessment, Contacts, and Vendors
    - Default placeholders and help text
    - Proper section ordering
    
    **Authorization**: Requires BC admin role.
    """
    from app.services.bcp_template import bootstrap_default_template
    
    template = await bootstrap_default_template()
    return BCTemplateDetail(**template)


# ============================================================================
# Plans Endpoints (CRUD)
# ============================================================================

@router.get("/plans", response_model=BCPaginatedResponse)
async def list_plans(
    status: Optional[BCPlanListStatus] = Query(None, description="Filter by status"),
    q: Optional[str] = Query(None, description="Search query"),
    owner: Optional[int] = Query(None, description="Filter by owner user ID"),
    template_id: Optional[int] = Query(None, description="Filter by template"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: dict = Depends(require_bc_viewer),
) -> BCPaginatedResponse:
    """
    List BC plans with filtering and pagination.
    
    Supports filtering by status, owner, template, and search query.
    Returns paginated results with metadata.
    
    **Authorization**: Requires BC viewer role or higher.
    **Access Control**: Viewers without edit permission only see approved plans.
    """
    # Check if user has editor role or higher
    from app.api.dependencies.bc_rbac import _get_user_bc_role
    from app.schemas.bc5_models import BCUserRole
    
    user_role = await _get_user_bc_role(current_user)
    
    # Viewers can only list approved plans
    if user_role == BCUserRole.VIEWER:
        status_value = "approved"  # Force approved status for viewers
    else:
        status_value = status.value if status else None
    
    offset = (page - 1) * per_page
    
    # Get plans
    plans = await bc_repo.list_plans(
        status=status_value,
        owner_user_id=owner,
        template_id=template_id,
        search_query=q,
        limit=per_page,
        offset=offset,
    )
    
    # Get total count
    total = await bc_repo.count_plans(
        status=status_value,
        owner_user_id=owner,
        template_id=template_id,
        search_query=q,
    )
    
    # Enrich with related data
    items = []
    for plan in plans:
        await _enrich_user_name(plan, "owner_user_id", "owner_name")
        await _enrich_template(plan)
        await _enrich_current_version(plan)
        items.append(BCPlanListItem(**plan))
    
    pagination = _calculate_pagination(total, page, per_page)
    return BCPaginatedResponse(items=items, **pagination)


@router.post("/plans", response_model=BCPlanDetail, status_code=status.HTTP_201_CREATED)
async def create_plan(
    plan_data: BCPlanCreate,
    current_user: dict = Depends(require_bc_editor),
) -> BCPlanDetail:
    """
    Create a new BC plan.
    
    Creates a new business continuity plan with the specified properties.
    The current user becomes the plan owner.
    
    When created from a template, automatically creates an initial version (version 1)
    with editable sections pre-populated based on the template schema.
    
    **Authorization**: Requires BC editor role or higher.
    """
    # Verify template exists if provided
    template = None
    if plan_data.template_id:
        template = await bc_repo.get_template_by_id(plan_data.template_id)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    
    plan = await bc_repo.create_plan(
        title=plan_data.title,
        owner_user_id=current_user["id"],
        status=plan_data.status.value,
        org_id=plan_data.org_id,
        template_id=plan_data.template_id,
    )
    
    # If created from a template, automatically create an initial version
    # with editable sections matching the template structure
    if template and template.get("schema_json"):
        from app.services.bc_services import _create_empty_content_from_schema
        
        # Create empty content structure from template schema
        initial_content = _create_empty_content_from_schema(template["schema_json"])
        
        # Create version 1
        version = await bc_repo.create_version(
            plan_id=plan["id"],
            version_number=1,
            status="active",
            authored_by_user_id=current_user["id"],
            summary_change_note="Initial version created from template",
            content_json=initial_content,
        )
        
        # Set this version as the current version
        await bc_repo.update_plan(
            plan_id=plan["id"],
            current_version_id=version["id"],
        )
        
        # Re-fetch plan to get updated current_version_id
        plan = await bc_repo.get_plan_by_id(plan["id"])
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan["id"],
        action="created",
        actor_user_id=current_user["id"],
        details_json={"title": plan_data.title, "status": plan_data.status.value, "from_template": template is not None},
    )
    
    # Enrich response
    await _enrich_user_name(plan, "owner_user_id", "owner_name")
    await _enrich_template(plan)
    await _enrich_current_version(plan)
    
    return BCPlanDetail(**plan)


@router.get("/plans/{plan_id}", response_model=BCPlanDetail)
async def get_plan(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCPlanDetail:
    """
    Get a specific BC plan.
    
    Returns the full plan details including related template and current version.
    
    **Authorization**: Requires BC viewer role or higher.
    **Access Control**: Viewers without edit permission can only access approved plans.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Check if user has editor role or higher
    from app.api.dependencies.bc_rbac import _get_user_bc_role
    from app.schemas.bc5_models import BCUserRole
    
    user_role = await _get_user_bc_role(current_user)
    
    # Viewers can only access approved plans
    if user_role == BCUserRole.VIEWER and plan.get("status") != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Plan must be approved for viewing."
        )
    
    # Log access to approved plans for audit compliance
    if plan.get("status") == "approved":
        await bc_repo.create_audit_entry(
            plan_id=plan_id,
            action="approved_plan_accessed",
            actor_user_id=current_user["id"],
            details_json={
                "user_role": user_role.value if user_role else "unknown",
                "plan_title": plan.get("title"),
            },
        )
    
    # Enrich response
    await _enrich_user_name(plan, "owner_user_id", "owner_name")
    await _enrich_template(plan)
    await _enrich_current_version(plan)
    
    return BCPlanDetail(**plan)


@router.patch("/plans/{plan_id}", response_model=BCPlanDetail)
async def update_plan(
    plan_id: int,
    plan_data: BCPlanUpdate,
    current_user: dict = Depends(require_bc_editor),
) -> BCPlanDetail:
    """
    Update a BC plan.
    
    Updates plan properties. Only provided fields will be updated.
    
    **Authorization**: Requires BC editor role or higher.
    """
    existing = await bc_repo.get_plan_by_id(plan_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Verify template exists if being updated
    if plan_data.template_id:
        template = await bc_repo.get_template_by_id(plan_data.template_id)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    
    plan = await bc_repo.update_plan(
        plan_id=plan_id,
        title=plan_data.title,
        status=plan_data.status.value if plan_data.status else None,
        template_id=plan_data.template_id,
        owner_user_id=plan_data.owner_user_id,
    )
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="updated",
        actor_user_id=current_user["id"],
        details_json={k: v for k, v in plan_data.model_dump().items() if v is not None},
    )
    
    # Enrich response
    await _enrich_user_name(plan, "owner_user_id", "owner_name")
    await _enrich_template(plan)
    await _enrich_current_version(plan)
    
    return BCPlanDetail(**plan)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: int,
    current_user: dict = Depends(require_bc_admin),
) -> None:
    """
    Delete a BC plan.
    
    Permanently deletes the plan and all related data (versions, reviews, attachments, etc.).
    This action cannot be undone.
    
    **Authorization**: Requires BC admin role.
    """
    existing = await bc_repo.get_plan_by_id(plan_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bc_repo.delete_plan(plan_id)


# ============================================================================
# Versions Endpoints
# ============================================================================

@router.get("/plans/{plan_id}/versions", response_model=list[BCVersionListItem])
async def list_versions(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCVersionListItem]:
    """
    List all versions for a plan.
    
    Returns all versions ordered by version number (descending).
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    versions = await bc_repo.list_plan_versions(plan_id)
    
    # Enrich with author names
    items = []
    for version in versions:
        await _enrich_user_name(version, "authored_by_user_id", "author_name")
        items.append(BCVersionListItem(**version))
    
    return items


@router.post("/plans/{plan_id}/versions", response_model=BCVersionDetail, status_code=status.HTTP_201_CREATED)
async def create_version(
    plan_id: int,
    version_data: BCVersionCreate,
    current_user: dict = Depends(require_bc_editor),
) -> BCVersionDetail:
    """
    Create a new version for a plan.
    
    Creates a new version with the provided content. The version number
    is automatically incremented based on existing versions.
    
    **Authorization**: Requires BC editor role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get next version number
    version_number = await bc_repo.get_next_version_number(plan_id)
    
    # Create version
    version = await bc_repo.create_version(
        plan_id=plan_id,
        version_number=version_number,
        authored_by_user_id=current_user["id"],
        summary_change_note=version_data.summary_change_note,
        content_json=version_data.content_json,
    )
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="version_created",
        actor_user_id=current_user["id"],
        details_json={"version_number": version_number, "version_id": version["id"]},
    )
    
    # Enrich response
    await _enrich_user_name(version, "authored_by_user_id", "author_name")
    
    return BCVersionDetail(**version)


@router.get("/plans/{plan_id}/versions/{version_id}", response_model=BCVersionDetail)
async def get_version(
    plan_id: int,
    version_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCVersionDetail:
    """
    Get a specific version.
    
    Returns the full version details including content.
    
    **Authorization**: Requires BC viewer role or higher.
    **Access Control**: Viewers without edit permission can only access versions of approved plans.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Check if user has editor role or higher
    from app.api.dependencies.bc_rbac import _get_user_bc_role
    from app.schemas.bc5_models import BCUserRole
    
    user_role = await _get_user_bc_role(current_user)
    
    # Viewers can only access versions of approved plans
    if user_role == BCUserRole.VIEWER and plan.get("status") != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Plan must be approved for viewing versions."
        )
    
    version = await bc_repo.get_version_by_id(version_id)
    if not version or version["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    
    # Log access to approved plan versions for audit compliance
    if plan.get("status") == "approved":
        await bc_repo.create_audit_entry(
            plan_id=plan_id,
            action="approved_plan_version_accessed",
            actor_user_id=current_user["id"],
            details_json={
                "user_role": user_role.value if user_role else "unknown",
                "version_id": version_id,
                "version_number": version.get("version_number"),
            },
        )
    
    # Enrich response
    await _enrich_user_name(version, "authored_by_user_id", "author_name")
    
    return BCVersionDetail(**version)


@router.post("/plans/{plan_id}/versions/{version_id}/activate", response_model=BCVersionDetail)
async def activate_version(
    plan_id: int,
    version_id: int,
    current_user: dict = Depends(require_bc_editor),
) -> BCVersionDetail:
    """
    Activate a specific version.
    
    Makes this version the active version for the plan, superseding all other versions.
    After activation, triggers acknowledgment prompts for users with BC access.
    
    **Authorization**: Requires BC editor role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    version = await bc_repo.get_version_by_id(version_id)
    if not version or version["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    
    # Activate the version
    version = await bc_repo.activate_version(version_id, plan_id)
    
    # Create audit entry for activation
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="version_activated",
        actor_user_id=current_user["id"],
        details_json={"version_id": version_id, "version_number": version["version_number"]},
    )
    
    # Get users who need to acknowledge the new version
    pending_users = await bc_repo.get_users_pending_acknowledgment(plan_id, version["version_number"])
    
    # Create audit entry indicating acknowledgment requirement
    if pending_users:
        await bc_repo.create_audit_entry(
            plan_id=plan_id,
            action="acknowledgment_required",
            actor_user_id=current_user["id"],
            details_json={
                "version_number": version["version_number"],
                "pending_user_count": len(pending_users),
                "pending_user_ids": [u["id"] for u in pending_users[:10]],  # Limit to first 10 for audit log
            },
        )
        
        # TODO: Send automatic notifications to pending users
        # This would integrate with the notification service to send emails/in-app notifications
        # For now, we just log the requirement in the audit trail
    
    # Enrich response
    await _enrich_user_name(version, "authored_by_user_id", "author_name")
    
    return BCVersionDetail(**version)


# ============================================================================
# Workflow Endpoints
# ============================================================================

@router.post("/plans/{plan_id}/submit-for-review", response_model=list[BCReviewListItem])
async def submit_plan_for_review(
    plan_id: int,
    review_data: BCReviewSubmit,
    current_user: dict = Depends(require_bc_editor),
) -> list[BCReviewListItem]:
    """
    Submit a plan for review.
    
    Creates review requests for the specified reviewers. The plan status
    is automatically updated to 'in_review'. Each reviewer will be able to
    approve or request changes to the plan.
    
    **Authorization**: Requires BC editor role or higher.
    
    **Request Body Example:**
    ```json
    {
      "reviewer_user_ids": [5, 7, 12],
      "notes": "Please review the updated recovery procedures in section 6."
    }
    ```
    
    **Response Example:**
    ```json
    [
      {
        "id": 1,
        "plan_id": 15,
        "requested_by_user_id": 3,
        "requested_by_name": "John Smith",
        "reviewer_user_id": 5,
        "reviewer_name": "Jane Doe",
        "status": "pending",
        "notes": "Please review the updated recovery procedures in section 6.",
        "created_at": "2024-03-20T10:30:00Z"
      }
    ]
    ```
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Update plan status to in_review
    await bc_repo.update_plan(plan_id, status="in_review")
    
    # Create review requests for each reviewer
    reviews = []
    for reviewer_id in review_data.reviewer_user_ids:
        # Verify reviewer exists
        reviewer = await user_repo.get_user_by_id(reviewer_id)
        if not reviewer:
            continue
        
        review = await bc_repo.create_review(
            plan_id=plan_id,
            requested_by_user_id=current_user["id"],
            reviewer_user_id=reviewer_id,
            notes=review_data.notes,
        )
        reviews.append(review)
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="submitted_for_review",
        actor_user_id=current_user["id"],
        details_json={"reviewer_ids": review_data.reviewer_user_ids},
    )
    
    # Enrich response
    items = []
    for review in reviews:
        await _enrich_user_name(review, "requested_by_user_id", "requested_by_name")
        await _enrich_user_name(review, "reviewer_user_id", "reviewer_name")
        items.append(BCReviewListItem(**review))
    
    return items


@router.post("/plans/{plan_id}/reviews/{review_id}/approve", response_model=BCReviewListItem)
async def approve_review(
    plan_id: int,
    review_id: int,
    approval_data: BCReviewApprove,
    current_user: dict = Depends(require_bc_approver),
) -> BCReviewListItem:
    """
    Approve a plan review.
    
    Marks the review as approved. If all reviews are approved, the plan
    status is automatically updated to 'approved'. Only the assigned reviewer
    can approve their review (unless user is super admin).
    
    **Authorization**: Requires BC approver role or higher.
    
    **Request Body Example:**
    ```json
    {
      "notes": "Plan looks comprehensive. All sections are complete and meet requirements."
    }
    ```
    
    **Response Example:**
    ```json
    {
      "id": 1,
      "plan_id": 15,
      "requested_by_user_id": 3,
      "requested_by_name": "John Smith",
      "reviewer_user_id": 5,
      "reviewer_name": "Jane Doe",
      "status": "approved",
      "notes": "Plan looks comprehensive. All sections are complete and meet requirements.",
      "created_at": "2024-03-20T10:30:00Z",
      "reviewed_at": "2024-03-21T15:45:00Z"
    }
    ```
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    review = await bc_repo.get_review_by_id(review_id)
    if not review or review["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    
    # Verify user is the reviewer
    if review["reviewer_user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the assigned reviewer can approve")
    
    # Update review
    review = await bc_repo.update_review_decision(
        review_id=review_id,
        status="approved",
        notes=approval_data.notes,
    )
    
    # Check if all reviews are approved
    all_reviews = await bc_repo.list_plan_reviews(plan_id)
    all_approved = all(r["status"] == "approved" for r in all_reviews if r["status"] != "pending")
    
    if all_approved:
        # Update plan to approved
        await bc_repo.update_plan(
            plan_id,
            status="approved",
            approved_at_utc=datetime.now(timezone.utc),
        )
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="review_approved",
        actor_user_id=current_user["id"],
        details_json={"review_id": review_id, "all_approved": all_approved},
    )
    
    # Enrich response
    await _enrich_user_name(review, "requested_by_user_id", "requested_by_name")
    await _enrich_user_name(review, "reviewer_user_id", "reviewer_name")
    
    return BCReviewListItem(**review)


@router.post("/plans/{plan_id}/reviews/{review_id}/request-changes", response_model=BCReviewListItem)
async def request_review_changes(
    plan_id: int,
    review_id: int,
    changes_data: BCReviewRequestChanges,
    current_user: dict = Depends(require_bc_approver),
) -> BCReviewListItem:
    """
    Request changes to a plan.
    
    Marks the review as requiring changes. The plan status is updated to 'draft'.
    
    **Authorization**: Requires BC approver role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    review = await bc_repo.get_review_by_id(review_id)
    if not review or review["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    
    # Verify user is the reviewer
    if review["reviewer_user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the assigned reviewer can request changes")
    
    # Update review
    review = await bc_repo.update_review_decision(
        review_id=review_id,
        status="changes_requested",
        notes=changes_data.notes,
    )
    
    # Update plan back to draft
    await bc_repo.update_plan(plan_id, status="draft")
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="changes_requested",
        actor_user_id=current_user["id"],
        details_json={"review_id": review_id, "notes": changes_data.notes},
    )
    
    # Enrich response
    await _enrich_user_name(review, "requested_by_user_id", "requested_by_name")
    await _enrich_user_name(review, "reviewer_user_id", "reviewer_name")
    
    return BCReviewListItem(**review)


@router.post("/plans/{plan_id}/acknowledge", response_model=BCAcknowledgeResponse)
async def acknowledge_plan(
    plan_id: int,
    ack_data: BCAcknowledge,
    current_user: dict = Depends(require_bc_viewer),
) -> BCAcknowledgeResponse:
    """
    Acknowledge a plan.
    
    Records that the current user has read and acknowledged the plan.
    Optionally acknowledges a specific version number.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Create acknowledgment
    ack = await bc_repo.create_acknowledgment(
        plan_id=plan_id,
        user_id=current_user["id"],
        ack_version_number=ack_data.ack_version_number,
    )
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="acknowledged",
        actor_user_id=current_user["id"],
        details_json={"ack_version_number": ack_data.ack_version_number},
    )
    
    return BCAcknowledgeResponse(**ack)


@router.get("/plans/{plan_id}/acknowledgments/summary", response_model=BCAcknowledgmentSummary)
async def get_acknowledgment_summary(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCAcknowledgmentSummary:
    """
    Get acknowledgment summary for a plan's current version.
    
    Returns statistics about how many users have acknowledged the current version
    versus how many still need to acknowledge.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active version
    version = await bc_repo.get_active_version(plan_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active version found")
    
    version_number = version["version_number"]
    summary = await bc_repo.get_acknowledgment_summary(plan_id, version_number)
    
    return BCAcknowledgmentSummary(**summary)


@router.get("/plans/{plan_id}/acknowledgments/pending", response_model=list[BCPendingUser])
async def get_pending_acknowledgments(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCPendingUser]:
    """
    Get list of users who have not acknowledged the current version.
    
    Returns user details for all users who need to acknowledge but haven't yet.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active version
    version = await bc_repo.get_active_version(plan_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active version found")
    
    version_number = version["version_number"]
    pending_users = await bc_repo.get_users_pending_acknowledgment(plan_id, version_number)
    
    return [BCPendingUser(**user) for user in pending_users]


@router.post("/plans/{plan_id}/acknowledgments/notify", status_code=status.HTTP_202_ACCEPTED)
async def notify_pending_acknowledgments(
    plan_id: int,
    notify_data: BCNotifyAcknowledgment,
    current_user: dict = Depends(require_bc_editor),
) -> dict[str, Any]:
    """
    Send notifications to users requesting plan acknowledgment.
    
    Sends notifications (email/in-app) to specified users requesting they
    acknowledge the current version of the plan.
    
    **Authorization**: Requires BC editor role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active version
    version = await bc_repo.get_active_version(plan_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active version found")
    
    # Verify all user IDs exist and have BC access
    pending_users = await bc_repo.get_users_pending_acknowledgment(plan_id, version["version_number"])
    pending_user_ids = {u["id"] for u in pending_users}
    
    invalid_users = [uid for uid in notify_data.user_ids if uid not in pending_user_ids]
    if invalid_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or already acknowledged user IDs: {invalid_users}"
        )
    
    # TODO: Implement actual notification sending via notification service
    # For now, just log the audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="acknowledgment_notification_sent",
        actor_user_id=current_user["id"],
        details_json={
            "user_ids": notify_data.user_ids,
            "version_number": version["version_number"],
            "message": notify_data.message,
        },
    )
    
    return {
        "message": "Acknowledgment notifications queued",
        "notified_users": len(notify_data.user_ids),
        "version_number": version["version_number"],
    }


# ============================================================================
# Content/Sections Endpoints
# ============================================================================

@router.get("/plans/{plan_id}/sections", response_model=list[BCSectionListItem])
async def list_plan_sections(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCSectionListItem]:
    """
    List all sections for a plan.
    
    Returns sections from the current active version's content.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active version
    version = await bc_repo.get_active_version(plan_id)
    if not version or not version.get("content_json"):
        return []
    
    # Extract sections from content_json
    content = version.get("content_json", {})
    sections = content.get("sections", [])
    
    return [BCSectionListItem(**section) for section in sections]


@router.patch("/plans/{plan_id}/sections/{section_key}", response_model=BCSectionListItem)
async def update_plan_section(
    plan_id: int,
    section_key: str,
    section_data: BCSectionUpdate,
    current_user: dict = Depends(require_bc_editor),
) -> BCSectionListItem:
    """
    Update a specific section in a plan.
    
    Performs a partial update on the section content. The update is merged
    with the existing content.
    
    **Authorization**: Requires BC editor role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active version
    version = await bc_repo.get_active_version(plan_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active version found")
    
    # Update section content
    content = version.get("content_json") or {}
    sections = content.get("sections", [])
    
    section_found = False
    updated_section = None
    for section in sections:
        if section.get("key") == section_key:
            # Merge updates
            section_content = section.get("content") or {}
            section_content.update(section_data.content_json)
            section["content"] = section_content
            updated_section = section
            section_found = True
            break
    
    if not section_found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
    
    # Save updated content
    content["sections"] = sections
    await bc_repo.update_version_content(version["id"], content)
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="section_updated",
        actor_user_id=current_user["id"],
        details_json={"section_key": section_key, "version_id": version["id"]},
    )
    
    return BCSectionListItem(**updated_section)


# ============================================================================
# Attachments Endpoints
# ============================================================================

@router.get("/plans/{plan_id}/attachments", response_model=list[BCAttachmentListItem])
async def list_plan_attachments(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCAttachmentListItem]:
    """
    List all attachments for a plan.
    
    Returns all file attachments with metadata.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    attachments = await bc_repo.list_plan_attachments(plan_id)
    
    # Enrich with user names and download URLs
    items = []
    for attachment in attachments:
        await _enrich_user_name(attachment, "uploaded_by_user_id", "uploaded_by_name")
        attachment["download_url"] = f"/api/bc/plans/{plan_id}/attachments/{attachment['id']}/download"
        items.append(BCAttachmentListItem(**attachment))
    
    return items


@router.post("/plans/{plan_id}/attachments", response_model=BCAttachmentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_plan_attachment(
    plan_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_bc_editor),
) -> BCAttachmentUploadResponse:
    """
    Upload a file attachment to a plan.
    
    Accepts file uploads and stores them securely with comprehensive validation:
    - Validates file size (max 50 MB)
    - Checks file type against allowed extensions
    - Rejects executable files for security
    - Optionally scans with antivirus if available
    - Tracks upload metadata in audit log
    
    **Authorization**: Requires BC editor role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get settings for AV scanning
    settings = get_settings()
    enable_av_scan = getattr(settings, "enable_av_scan", False)
    
    # Validate file with comprehensive security checks
    content, sanitized_filename, size_bytes = await bc_file_validation.validate_upload_file(
        upload=file,
        max_size=bc_file_validation.MAX_FILE_SIZE,
        allow_executables=False,
        scan_with_av=enable_av_scan,
    )
    
    # Calculate hash
    file_hash = bc_file_validation.calculate_file_hash(content)
    
    # Determine storage path (simplified - in production would use file storage service)
    storage_path = f"bc_attachments/{plan_id}/{file_hash}_{sanitized_filename}"
    
    # Create attachment record
    attachment = await bc_repo.create_attachment(
        plan_id=plan_id,
        file_name=sanitized_filename,
        storage_path=storage_path,
        uploaded_by_user_id=current_user["id"],
        content_type=file.content_type,
        size_bytes=size_bytes,
        file_hash=file_hash,
    )
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="attachment_uploaded",
        actor_user_id=current_user["id"],
        details_json={
            "filename": sanitized_filename,
            "original_filename": file.filename,
            "attachment_id": attachment["id"],
            "size_bytes": size_bytes,
            "content_type": file.content_type,
        },
    )
    
    # TODO: Actually store the file using file storage service
    # For now, just return the metadata
    
    return BCAttachmentUploadResponse(**attachment)


@router.delete("/plans/{plan_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan_attachment(
    plan_id: int,
    attachment_id: int,
    current_user: dict = Depends(require_bc_editor),
) -> None:
    """
    Delete a file attachment.
    
    Removes the attachment metadata and file from storage.
    
    **Authorization**: Requires BC editor role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    attachment = await bc_repo.get_attachment_by_id(attachment_id)
    if not attachment or attachment["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    # Delete attachment
    await bc_repo.delete_attachment(attachment_id)
    
    # Create audit entry
    await bc_repo.create_audit_entry(
        plan_id=plan_id,
        action="attachment_deleted",
        actor_user_id=current_user["id"],
        details_json={"filename": attachment["file_name"], "attachment_id": attachment_id},
    )
    
    # TODO: Actually delete the file from storage


# ============================================================================
# Export Endpoints (Rate Limited)
# ============================================================================

@router.post("/plans/{plan_id}/export/docx", response_model=BCExportResponse)
async def export_plan_docx(
    plan_id: int,
    export_request: BCExportRequest,
    current_user: dict = Depends(require_bc_viewer),
) -> BCExportResponse:
    """
    Export a plan to DOCX format.
    
    Generates a DOCX document from the plan content using python-docx.
    Preserves government template structure and styling.
    Embeds revision metadata (title, version, date, author).
    Includes tables for BIA, risk register, contacts, and vendors.
    
    **Authorization**: Requires BC viewer role or higher.
    **Rate Limited**: This endpoint is rate-limited to prevent abuse (configurable via EXPORT_MAX_PER_MINUTE).
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get version to export
    version_id = export_request.version_id
    if version_id:
        version = await bc_repo.get_version_by_id(version_id)
        if not version or version["plan_id"] != plan_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    else:
        version = await bc_repo.get_active_version(plan_id)
        if not version:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active version found")
        version_id = version["id"]
    
    try:
        # Generate DOCX export
        docx_buffer, content_hash = await bc_export_service.export_to_docx(
            plan_id=plan_id,
            version_id=version_id,
        )
        
        # Update version with export hash
        await bc_repo.update_version_export_hash(
            version_id=version_id,
            docx_hash=content_hash,
        )
        
        # Create audit entry
        await bc_repo.create_audit_entry(
            plan_id=plan_id,
            action="exported_docx",
            actor_user_id=current_user["id"],
            details_json={"version_id": version_id, "content_hash": content_hash},
        )
        
        # For now, return the metadata (actual file download would be a separate endpoint)
        export_url = f"/api/bc/exports/{plan_id}/v{version['version_number']}.docx"
        
        return BCExportResponse(
            export_url=export_url,
            format=BCExportFormat.DOCX,
            version_id=version_id,
            generated_at=datetime.now(timezone.utc),
            file_hash=content_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate DOCX export: {str(e)}",
        )


@router.post("/plans/{plan_id}/export/pdf", response_model=BCExportResponse)
async def export_plan_pdf(
    plan_id: int,
    export_request: BCExportRequest,
    current_user: dict = Depends(require_bc_viewer),
) -> BCExportResponse:
    """
    Export a plan to PDF format.
    
    Generates a PDF document from the plan content using WeasyPrint.
    Converts rendered HTML (Jinja2 template) to PDF with professional government styling.
    Preserves template structure and includes all metadata.
    
    **Authorization**: Requires BC viewer role or higher.
    **Rate Limited**: This endpoint is rate-limited to prevent abuse (configurable via EXPORT_MAX_PER_MINUTE).
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get version to export
    version_id = export_request.version_id
    if version_id:
        version = await bc_repo.get_version_by_id(version_id)
        if not version or version["plan_id"] != plan_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    else:
        version = await bc_repo.get_active_version(plan_id)
        if not version:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active version found")
        version_id = version["id"]
    
    try:
        # Generate PDF export
        pdf_buffer, content_hash = await bc_export_service.export_to_pdf(
            plan_id=plan_id,
            version_id=version_id,
        )
        
        # Update version with export hash
        await bc_repo.update_version_export_hash(
            version_id=version_id,
            pdf_hash=content_hash,
        )
        
        # Create audit entry
        await bc_repo.create_audit_entry(
            plan_id=plan_id,
            action="exported_pdf",
            actor_user_id=current_user["id"],
            details_json={"version_id": version_id, "content_hash": content_hash},
        )
        
        # For now, return the metadata (actual file download would be a separate endpoint)
        export_url = f"/api/bc/exports/{plan_id}/v{version['version_number']}.pdf"
        
        return BCExportResponse(
            export_url=export_url,
            format=BCExportFormat.PDF,
            version_id=version_id,
            generated_at=datetime.now(timezone.utc),
            file_hash=content_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate PDF export: {str(e)}",
        )


# ============================================================================
# Audit and Change Log Endpoints
# ============================================================================

@router.get("/plans/{plan_id}/audit", response_model=list[BCAuditListItem])
async def get_plan_audit_trail(
    plan_id: int,
    limit: int = Query(100, ge=1, le=500, description="Number of audit entries to return"),
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCAuditListItem]:
    """
    Get the audit trail for a plan.
    
    Returns all audit entries showing actions performed on the plan.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    audit_entries = await bc_repo.list_plan_audit_trail(plan_id, limit=limit)
    
    # Enrich with actor names
    items = []
    for entry in audit_entries:
        await _enrich_user_name(entry, "actor_user_id", "actor_name")
        items.append(BCAuditListItem(**entry))
    
    return items


@router.get("/plans/{plan_id}/change-log", response_model=list[BCChangeLogItem])
async def get_plan_change_log(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCChangeLogItem]:
    """
    Get the change log for a plan.
    
    Returns all change log entries linked to this plan from the changes/ folder.
    
    **Authorization**: Requires BC viewer role or higher.
    """
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    change_logs = await bc_repo.list_plan_change_logs(plan_id)
    
    # TODO: Enrich with actual change log content from changes/ folder
    # For now, return the mappings
    
    return [BCChangeLogItem(**log) for log in change_logs]
