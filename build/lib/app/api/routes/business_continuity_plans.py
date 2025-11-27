from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.repositories import business_continuity_plans as bc_plans_repo
from app.schemas.bcp_template import BCPTemplateSchema
from app.schemas.business_continuity_plans import (
    BusinessContinuityPlanCreate,
    BusinessContinuityPlanListItem,
    BusinessContinuityPlanResponse,
    BusinessContinuityPlanUpdate,
    PermissionLevel,
    PlanPermissionCreate,
    PlanPermissionResponse,
    PlanPermissionUpdate,
    PlanStatus,
    PlanType,
)
from app.services.bcp_template import get_default_government_bcp_template

router = APIRouter(prefix="/api/business-continuity-plans", tags=["Business Continuity Plans"])


@router.get("/template/default", response_model=BCPTemplateSchema)
async def get_default_template(
    current_user: dict = Depends(get_current_user),
) -> BCPTemplateSchema:
    """
    Get the default government BCP template schema.
    
    Returns a comprehensive template schema for government business continuity plans,
    including all standard sections, fields, and metadata. This schema can be used
    to understand the structure of a BCP or to generate forms and data collection
    interfaces.
    
    The template includes:
    - Plan Overview: purpose, scope, objectives, assumptions
    - Governance & Roles: roles, responsibilities, escalation matrix, approvals
    - Business Impact Analysis (BIA): critical processes, impact categories, RTO, RPO, MTPD, dependencies
    - Risk Assessment: threats, likelihood, impact, risk rating, mitigations
    - Recovery Strategies: process-level strategies, workarounds, resource needs
    - Incident Response: activation criteria, notification tree, step-by-step procedures
    - Communications Plan: internal, external, regulators, media, templates
    - IT/Systems Recovery: apps, infra, DR runbooks, backup/restore, test cadence
    - Testing & Exercises: schedule, scenarios, evidence, outcomes
    - Maintenance & Review: review cadence, owners, change process
    - Appendices: contact lists, vendor SLAs, site info, inventories, floor plans
    - Revision History: version, author, date, summary
    
    Field types include: text, rich_text (HTML), date, datetime, select, multiselect,
    integer, decimal, boolean, table, file, contact_ref, user_ref, url.
    
    **Example Response:**
    ```json
    {
      "name": "Government BCP Template",
      "version": "1.0",
      "sections": [
        {
          "key": "plan_overview",
          "title": "Plan Overview",
          "description": "High-level purpose and scope of the plan",
          "fields": [
            {
              "key": "purpose",
              "label": "Purpose",
              "type": "rich_text",
              "required": true,
              "placeholder": "Describe the purpose of this plan..."
            }
          ]
        }
      ]
    }
    ```
    
    **Authorization:** Requires authenticated user (any role).
    """
    return get_default_government_bcp_template()


@router.get("/", response_model=list[BusinessContinuityPlanListItem])
async def list_plans(
    plan_type: PlanType | None = Query(None, description="Filter by plan type"),
    status: PlanStatus | None = Query(None, description="Filter by status"),
    current_user: dict = Depends(get_current_user),
) -> list[BusinessContinuityPlanListItem]:
    """
    List all business continuity plans accessible to the current user.
    
    Returns plans based on user permissions:
    - Super admins can view all plans
    - Regular users can only view plans they have explicit access to
    
    Plans can be filtered by type (disaster_recovery, incident_response, business_continuity)
    and status (draft, active, archived).
    
    **Query Parameters:**
    - `plan_type`: Filter by type (disaster_recovery, incident_response, business_continuity)
    - `status`: Filter by status (draft, active, archived)
    
    **Example Response:**
    ```json
    [
      {
        "id": 1,
        "title": "IT Disaster Recovery Plan",
        "plan_type": "disaster_recovery",
        "version": "2.1",
        "status": "active",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-03-20T14:45:00Z",
        "last_reviewed_at": "2024-03-20T14:45:00Z",
        "created_by": 5,
        "user_permission": "edit"
      }
    ]
    ```
    
    **Authorization:** Requires authenticated user.
    """
    user_id = current_user["id"]
    is_super_admin = current_user.get("is_super_admin", False)
    
    plan_type_value = plan_type.value if plan_type else None
    status_value = status.value if status else None
    
    plans = await bc_plans_repo.list_plans(
        plan_type=plan_type_value,
        status=status_value,
        user_id=user_id if not is_super_admin else None,
    )
    
    result = []
    for plan in plans:
        # Filter plans based on permissions
        if not is_super_admin:
            can_access = await bc_plans_repo.user_can_access_plan(plan["id"], user_id, is_super_admin)
            if not can_access:
                continue
        
        # Get user permission if not already included
        user_permission = plan.get("user_permission")
        if user_permission is None and not is_super_admin:
            user_permission = await bc_plans_repo.get_user_permission_for_plan(plan["id"], user_id)
        
        result.append(
            BusinessContinuityPlanListItem(
                id=plan["id"],
                title=plan["title"],
                plan_type=PlanType(plan["plan_type"]),
                version=plan["version"],
                status=PlanStatus(plan["status"]),
                created_at=plan["created_at"],
                updated_at=plan["updated_at"],
                last_reviewed_at=plan.get("last_reviewed_at"),
                created_by=plan["created_by"],
                user_permission=PermissionLevel(user_permission) if user_permission else None,
            )
        )
    
    return result


@router.post("/", response_model=BusinessContinuityPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    plan_data: BusinessContinuityPlanCreate,
    current_user: dict = Depends(require_super_admin),
) -> BusinessContinuityPlanResponse:
    """
    Create a new business continuity plan (super admin only).
    
    Creates a new DR/IR/BC plan with the specified type, content, and status.
    All timestamps are stored in UTC. The plan creator is automatically recorded.
    
    Plan types:
    - disaster_recovery: Plans for recovering from catastrophic failures
    - incident_response: Plans for responding to security incidents
    - business_continuity: Plans for maintaining business operations during disruptions
    
    **Request Body Example:**
    ```json
    {
      "title": "IT Disaster Recovery Plan",
      "plan_type": "disaster_recovery",
      "content": "# Disaster Recovery Plan\\n\\n## Purpose\\nThis plan outlines...",
      "version": "1.0",
      "status": "draft"
    }
    ```
    
    **Response Example:**
    ```json
    {
      "id": 1,
      "title": "IT Disaster Recovery Plan",
      "plan_type": "disaster_recovery",
      "content": "# Disaster Recovery Plan\\n\\n## Purpose\\nThis plan outlines...",
      "version": "1.0",
      "status": "draft",
      "created_by": 5,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z",
      "last_reviewed_at": null,
      "last_reviewed_by": null
    }
    ```
    
    **Authorization:** Requires super admin privileges.
    """
    plan = await bc_plans_repo.create_plan(
        title=plan_data.title,
        plan_type=plan_data.plan_type.value,
        content=plan_data.content,
        version=plan_data.version,
        status=plan_data.status.value,
        created_by=current_user["id"],
    )
    
    return BusinessContinuityPlanResponse(
        id=plan["id"],
        title=plan["title"],
        plan_type=PlanType(plan["plan_type"]),
        content=plan["content"],
        version=plan["version"],
        status=PlanStatus(plan["status"]),
        created_by=plan["created_by"],
        created_at=plan["created_at"],
        updated_at=plan["updated_at"],
        last_reviewed_at=plan.get("last_reviewed_at"),
        last_reviewed_by=plan.get("last_reviewed_by"),
    )


@router.get("/{plan_id}", response_model=BusinessContinuityPlanResponse)
async def get_plan(
    plan_id: int,
    current_user: dict = Depends(get_current_user),
) -> BusinessContinuityPlanResponse:
    """
    Get a specific business continuity plan.
    
    Returns the full plan details including content, version history, and review status.
    Access is controlled by permissions - users must have read or edit access to view a plan.
    Super admins can view any plan.
    """
    is_super_admin = current_user.get("is_super_admin", False)
    user_id = current_user["id"]
    
    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Check permissions
    can_access = await bc_plans_repo.user_can_access_plan(plan_id, user_id, is_super_admin)
    if not can_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    return BusinessContinuityPlanResponse(
        id=plan["id"],
        title=plan["title"],
        plan_type=PlanType(plan["plan_type"]),
        content=plan["content"],
        version=plan["version"],
        status=PlanStatus(plan["status"]),
        created_by=plan["created_by"],
        created_at=plan["created_at"],
        updated_at=plan["updated_at"],
        last_reviewed_at=plan.get("last_reviewed_at"),
        last_reviewed_by=plan.get("last_reviewed_by"),
    )


@router.put("/{plan_id}", response_model=BusinessContinuityPlanResponse)
async def update_plan(
    plan_id: int,
    plan_data: BusinessContinuityPlanUpdate,
    current_user: dict = Depends(get_current_user),
) -> BusinessContinuityPlanResponse:
    """
    Update a business continuity plan.
    
    Allows updating plan details including title, content, version, status, and review timestamp.
    Users must have edit permissions on the plan. Super admins can edit any plan.
    
    When last_reviewed_at is updated, the current user is automatically recorded as the reviewer.
    All partial updates are supported - only provided fields will be updated.
    """
    is_super_admin = current_user.get("is_super_admin", False)
    user_id = current_user["id"]
    
    # Check if plan exists
    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Check edit permissions
    can_edit = await bc_plans_repo.user_can_edit_plan(plan_id, user_id, is_super_admin)
    if not can_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Edit permission required")
    
    # Prepare update data
    update_data: dict[str, Any] = {}
    if plan_data.title is not None:
        update_data["title"] = plan_data.title
    if plan_data.plan_type is not None:
        update_data["plan_type"] = plan_data.plan_type.value
    if plan_data.content is not None:
        update_data["content"] = plan_data.content
    if plan_data.version is not None:
        update_data["version"] = plan_data.version
    if plan_data.status is not None:
        update_data["status"] = plan_data.status.value
    if plan_data.last_reviewed_at is not None:
        update_data["last_reviewed_at"] = plan_data.last_reviewed_at
        update_data["last_reviewed_by"] = user_id
    
    updated_plan = await bc_plans_repo.update_plan(plan_id, **update_data)
    
    return BusinessContinuityPlanResponse(
        id=updated_plan["id"],
        title=updated_plan["title"],
        plan_type=PlanType(updated_plan["plan_type"]),
        content=updated_plan["content"],
        version=updated_plan["version"],
        status=PlanStatus(updated_plan["status"]),
        created_by=updated_plan["created_by"],
        created_at=updated_plan["created_at"],
        updated_at=updated_plan["updated_at"],
        last_reviewed_at=updated_plan.get("last_reviewed_at"),
        last_reviewed_by=updated_plan.get("last_reviewed_by"),
    )


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: int,
    current_user: dict = Depends(require_super_admin),
) -> None:
    """
    Delete a business continuity plan (super admin only).
    
    Permanently removes a plan and all its associated permissions.
    This action cannot be undone. Use archive status instead if you want to preserve the plan.
    """
    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bc_plans_repo.delete_plan(plan_id)


@router.get("/{plan_id}/permissions", response_model=list[PlanPermissionResponse])
async def list_plan_permissions(
    plan_id: int,
    current_user: dict = Depends(require_super_admin),
) -> list[PlanPermissionResponse]:
    """
    List all permissions for a plan (super admin only).
    
    Returns all user and company-level permissions configured for the plan.
    Permissions can be at read or edit level. Super admins always have full access
    regardless of explicit permissions.
    """
    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    permissions = await bc_plans_repo.list_plan_permissions(plan_id)
    return [
        PlanPermissionResponse(
            id=perm["id"],
            plan_id=perm["plan_id"],
            user_id=perm.get("user_id"),
            company_id=perm.get("company_id"),
            permission_level=PermissionLevel(perm["permission_level"]),
            created_at=perm["created_at"],
        )
        for perm in permissions
    ]


@router.post("/{plan_id}/permissions", response_model=PlanPermissionResponse, status_code=status.HTTP_201_CREATED)
async def add_plan_permission(
    plan_id: int,
    permission_data: PlanPermissionCreate,
    current_user: dict = Depends(require_super_admin),
) -> PlanPermissionResponse:
    """
    Add a permission for a plan (super admin only).
    
    Grants read or edit access to a specific user or all users in a company.
    Either user_id or company_id must be provided, but not both.
    
    Permission levels:
    - read: View plan content only
    - edit: View and modify plan content
    """
    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    if permission_data.user_id is None and permission_data.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either user_id or company_id must be provided",
        )
    
    if permission_data.user_id is not None and permission_data.company_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only one of user_id or company_id can be provided",
        )
    
    try:
        perm = await bc_plans_repo.add_plan_permission(
            plan_id=plan_id,
            user_id=permission_data.user_id,
            company_id=permission_data.company_id,
            permission_level=permission_data.permission_level.value,
        )
    except Exception as e:
        # Handle duplicate key error
        if "Duplicate entry" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Permission already exists for this user/company",
            )
        raise
    
    return PlanPermissionResponse(
        id=perm["id"],
        plan_id=perm["plan_id"],
        user_id=perm.get("user_id"),
        company_id=perm.get("company_id"),
        permission_level=PermissionLevel(perm["permission_level"]),
        created_at=perm["created_at"],
    )


@router.put("/{plan_id}/permissions/{perm_id}", response_model=PlanPermissionResponse)
async def update_plan_permission(
    plan_id: int,
    perm_id: int,
    permission_data: PlanPermissionUpdate,
    current_user: dict = Depends(require_super_admin),
) -> PlanPermissionResponse:
    """
    Update a plan permission (super admin only).
    
    Changes the permission level for an existing user or company permission.
    Cannot change the user/company assignment - delete and recreate the permission instead.
    """
    perm = await bc_plans_repo.get_plan_permission_by_id(perm_id)
    if not perm or perm["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")
    
    if permission_data.permission_level is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="permission_level is required",
        )
    
    updated_perm = await bc_plans_repo.update_plan_permission(
        perm_id=perm_id,
        permission_level=permission_data.permission_level.value,
    )
    
    return PlanPermissionResponse(
        id=updated_perm["id"],
        plan_id=updated_perm["plan_id"],
        user_id=updated_perm.get("user_id"),
        company_id=updated_perm.get("company_id"),
        permission_level=PermissionLevel(updated_perm["permission_level"]),
        created_at=updated_perm["created_at"],
    )


@router.delete("/{plan_id}/permissions/{perm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan_permission(
    plan_id: int,
    perm_id: int,
    current_user: dict = Depends(require_super_admin),
) -> None:
    """
    Delete a plan permission (super admin only).
    
    Removes access for a user or company to a specific plan.
    Users will no longer be able to view or edit the plan unless they are super admins.
    """
    perm = await bc_plans_repo.get_plan_permission_by_id(perm_id)
    if not perm or perm["plan_id"] != plan_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")
    
    await bc_plans_repo.delete_plan_permission(perm_id)
