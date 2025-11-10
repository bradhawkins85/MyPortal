# BC7 Service Layer Documentation

## Overview

The BC7 service layer (`app/services/bc_services.py`) provides business logic functions for Business Continuity Planning (BCP) operations. It enforces business rules, manages workflows, and ensures data integrity across the BC system.

## Key Features

### 1. Status Transition Enforcement

The service layer enforces valid status transitions for business continuity plans:

```python
from app.services.bc_services import validate_status_transition
from app.schemas.bc5_models import BCPlanListStatus

# Valid transitions
validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.IN_REVIEW)  # ✓
validate_status_transition(BCPlanListStatus.IN_REVIEW, BCPlanListStatus.APPROVED)  # ✓
validate_status_transition(BCPlanListStatus.APPROVED, BCPlanListStatus.ARCHIVED)  # ✓
validate_status_transition(BCPlanListStatus.ARCHIVED, BCPlanListStatus.DRAFT)  # ✓ (reactivation)

# Invalid transitions (will raise HTTPException)
validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.APPROVED)  # ✗
```

**Allowed Transitions:**
- `draft` → `in_review`
- `in_review` → `approved` or `draft` (back to editing)
- `approved` → `archived`
- `archived` → `draft` (reactivation)

### 2. Template Schema Resolution and Content Merging

Resolve template schemas and merge them with plan content to ensure all required fields are present:

```python
from app.services.bc_services import resolve_template_and_merge_content

# Merge template schema with existing plan content
merged_content = await resolve_template_and_merge_content(
    template_id=1,
    plan_content={"overview": {"title": "My Plan"}}
)
# Returns: Complete content structure with template defaults filled in
```

### 3. Version Management

Automatically manage version numbers and supersede previous versions:

```python
from app.services.bc_services import create_new_version

# Create a new version
new_version = await create_new_version(
    plan_id=1,
    content_json={"overview": {"title": "Updated Plan"}},
    authored_by_user_id=5,
    summary_change_note="Updated recovery procedures",
    supersede_previous=True  # Marks previous versions as superseded
)
# Returns: New version with auto-incremented version number
```

### 4. Derived Field Computation

Compute derived values from plan data:

```python
from app.services.bc_services import compute_highest_risk_rating, compute_unacknowledged_users

# Get the highest risk rating across all risks in a plan
highest_risk = await compute_highest_risk_rating(plan_id=1)
# Returns: "critical", "high", "medium", "low", or None

# Get list of users who haven't acknowledged the plan
unacked_users = await compute_unacknowledged_users(
    plan_id=1,
    target_version_number=2  # Optional, defaults to current version
)
# Returns: List of user IDs
```

### 5. Audit Event Logging

Log all mutating actions for compliance and accountability:

```python
from app.services.bc_services import create_audit_event

# Log a plan update
await create_audit_event(
    plan_id=1,
    action="updated",
    actor_user_id=5,
    details={
        "field": "status",
        "old_value": "draft",
        "new_value": "in_review"
    }
)
```

### 6. Permission Enforcement

Enforce ownership and role-based permissions:

```python
from app.services.bc_services import (
    check_plan_ownership,
    enforce_plan_access,
    can_user_approve_plan
)
from app.schemas.bc5_models import BCUserRole

# Check if user owns a plan
is_owner = await check_plan_ownership(plan_id=1, user_id=5)

# Enforce access requirements (raises HTTPException if denied)
await enforce_plan_access(
    plan_id=1,
    user_id=5,
    required_role=BCUserRole.EDITOR,
    is_super_admin=False
)

# Check if user can approve a plan
can_approve = await can_user_approve_plan(
    plan_id=1,
    user_id=5,
    user_role=BCUserRole.APPROVER,
    is_super_admin=False
)
```

### 7. High-Level Workflow Functions

Convenience functions for common workflows:

```python
from app.services.bc_services import submit_plan_for_review, approve_plan, archive_plan

# Submit a plan for review
reviews = await submit_plan_for_review(
    plan_id=1,
    reviewer_user_ids=[10, 20, 30],
    requested_by_user_id=5,
    notes="Please review the updated procedures"
)

# Approve a plan
updated_plan = await approve_plan(
    plan_id=1,
    review_id=1,
    approver_user_id=10,
    notes="Looks good, approved"
)

# Archive a plan
archived_plan = await archive_plan(
    plan_id=1,
    archived_by_user_id=5,
    reason="Superseded by new version"
)
```

## Integration with API Endpoints

The service layer is designed to be used in API route handlers. Example:

```python
from fastapi import APIRouter, Depends
from app.api.dependencies.bc_rbac import require_bc_editor
from app.services.bc_services import submit_plan_for_review, create_audit_event

router = APIRouter()

@router.post("/api/bc/plans/{plan_id}/submit-for-review")
async def submit_for_review(
    plan_id: int,
    reviewer_ids: list[int],
    current_user: dict = Depends(require_bc_editor),
):
    # Use service layer function
    reviews = await submit_plan_for_review(
        plan_id=plan_id,
        reviewer_user_ids=reviewer_ids,
        requested_by_user_id=current_user["id"],
        notes="Please review"
    )
    
    # Audit logging is handled automatically by the service layer
    
    return {"reviews": reviews}
```

## Business Rules Enforced

1. **Status Transitions**: Only valid state transitions are allowed
2. **Version Numbers**: Automatically incremented, no duplicates
3. **Ownership**: Plan owners cannot approve their own plans
4. **Role-Based Access**: Approvers and admins can approve, editors cannot
5. **Audit Trail**: All mutating actions are logged with user and timestamp
6. **Template Compliance**: Plan content must conform to template schema

## Error Handling

All service functions raise `HTTPException` with appropriate status codes:

- `400 Bad Request`: Invalid transitions, validation errors
- `403 Forbidden`: Permission denied
- `404 Not Found`: Plan, template, or version not found

Example:

```python
from fastapi import HTTPException

try:
    await validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.APPROVED)
except HTTPException as e:
    print(f"Error {e.status_code}: {e.detail}")
    # Error 400: Invalid status transition from draft to approved. 
    # Allowed transitions: ['in_review']
```

## Testing

Comprehensive test suite available in `tests/test_bc7_services.py`:

```bash
pytest tests/test_bc7_services.py -v
```

38 tests covering:
- Status transition validation
- Template schema resolution
- Version management
- Derived field computation
- Audit logging
- Permission enforcement
- Workflow functions

## Dependencies

The service layer depends on:

- `app.repositories.bc3`: Database access layer
- `app.schemas.bc5_models`: Pydantic schemas for validation
- FastAPI `HTTPException` for error handling

## Future Enhancements

Potential improvements:

1. **Advanced Permission Models**: Support for team-based permissions
2. **Notification System**: Automatic notifications on status changes
3. **Approval Workflows**: Multi-stage approval processes
4. **Version Comparison**: Diff between versions
5. **Export Functions**: Generate DOCX/PDF exports with audit trail
