from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import compliance_checks as repo
from app.schemas.compliance_checks import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentSummary,
    AssignmentUpdate,
    AuditResponse,
    BulkAssignByCategory,
    CheckStatus,
    ComplianceCheckCategoryCreate,
    ComplianceCheckCategoryResponse,
    ComplianceCheckCategoryUpdate,
    ComplianceCheckCreate,
    ComplianceCheckResponse,
    ComplianceCheckUpdate,
    EvidenceCreate,
    EvidenceResponse,
)

router = APIRouter(prefix="/api/compliance-checks", tags=["Compliance Checks"])


def _assert_company_access(user: dict, company_id: int) -> None:
    """Raise 403 if a non-super-admin tries to access another company's data."""
    if user.get("is_super_admin"):
        return
    if user.get("company_id") != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access compliance checks for your own company",
        )


# =============================================================================
# Category endpoints (super-admin / library management)
# =============================================================================


@router.get("/categories", response_model=list[ComplianceCheckCategoryResponse])
async def list_categories(
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List all compliance check categories."""
    return await repo.list_categories()


@router.post(
    "/categories",
    response_model=ComplianceCheckCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    payload: ComplianceCheckCategoryCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Create a new compliance check category (super admin only)."""
    return await repo.create_category(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        is_system=payload.is_system,
    )


@router.patch("/categories/{category_id}", response_model=ComplianceCheckCategoryResponse)
async def update_category(
    category_id: int,
    payload: ComplianceCheckCategoryUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Update a compliance check category (super admin only)."""
    cat = await repo.get_category(category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    updated = await repo.update_category(category_id, **payload.model_dump(exclude_unset=True))
    return updated


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Delete a non-system category (super admin only)."""
    cat = await repo.get_category(category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    if cat.get("is_system"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="System categories cannot be deleted",
        )
    await repo.delete_category(category_id)


# =============================================================================
# Library check endpoints (super-admin / library management)
# =============================================================================


@router.get("/checks", response_model=list[ComplianceCheckResponse])
async def list_checks(
    category_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List compliance check library entries."""
    return await repo.list_checks(category_id=category_id, is_active=is_active, search=search)


@router.get("/checks/{check_id}", response_model=ComplianceCheckResponse)
async def get_check(
    check_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """Get a single compliance check from the library."""
    check = await repo.get_check(check_id)
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
    return check


@router.post(
    "/checks",
    response_model=ComplianceCheckResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_check(
    payload: ComplianceCheckCreate,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
):
    """Add a new check to the library (super admin only)."""
    return await repo.create_check(
        category_id=payload.category_id,
        code=payload.code,
        title=payload.title,
        description=payload.description,
        guidance=payload.guidance,
        default_review_interval_days=payload.default_review_interval_days,
        default_evidence_required=payload.default_evidence_required,
        is_predefined=payload.is_predefined,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
        created_by=user.get("id"),
    )


@router.patch("/checks/{check_id}", response_model=ComplianceCheckResponse)
async def update_check(
    check_id: int,
    payload: ComplianceCheckUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Update a library check (super admin only)."""
    check = await repo.get_check(check_id)
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
    updated = await repo.update_check(check_id, **payload.model_dump(exclude_unset=True))
    return updated


@router.delete("/checks/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_check(
    check_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Deactivate (soft-delete) a library check (super admin only)."""
    check = await repo.get_check(check_id)
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
    await repo.deactivate_check(check_id)


# =============================================================================
# Assignment endpoints (tenant-scoped)
# =============================================================================


@router.get(
    "/companies/{company_id}/assignments",
    response_model=list[AssignmentResponse],
)
async def list_assignments(
    company_id: int,
    status_filter: Optional[CheckStatus] = None,
    category_id: Optional[int] = None,
    overdue_only: bool = False,
    include_archived: bool = False,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List compliance check assignments for a company."""
    _assert_company_access(user, company_id)
    return await repo.list_assignments(
        company_id,
        status=status_filter,
        category_id=category_id,
        overdue_only=overdue_only,
        include_archived=include_archived,
    )


@router.get(
    "/companies/{company_id}/assignments/summary",
    response_model=AssignmentSummary,
)
async def get_assignment_summary(
    company_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Get a summary of compliance check assignments for a company."""
    _assert_company_access(user, company_id)
    return await repo.get_assignment_summary(company_id)


@router.post(
    "/companies/{company_id}/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment(
    company_id: int,
    payload: AssignmentCreate,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Assign a compliance check to a company."""
    _assert_company_access(user, company_id)

    existing = await repo.get_assignment_by_check(company_id, payload.check_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This check is already assigned to the company",
        )
    return await repo.create_assignment(
        company_id=company_id,
        check_id=payload.check_id,
        status=payload.status,
        review_interval_days=payload.review_interval_days,
        notes=payload.notes,
        owner_user_id=payload.owner_user_id,
    )


@router.post(
    "/companies/{company_id}/assignments/bulk-by-category",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_assign_by_category(
    company_id: int,
    payload: BulkAssignByCategory,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Assign all active checks from a category to a company (skips already-assigned checks)."""
    _assert_company_access(user, company_id)
    created_count = await repo.bulk_assign_by_category(company_id, payload.category_id)
    return {"message": "Bulk assignment complete", "created_count": created_count}


@router.get(
    "/companies/{company_id}/assignments/{assignment_id}",
    response_model=AssignmentResponse,
)
async def get_assignment(
    company_id: int,
    assignment_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Get a specific compliance check assignment."""
    _assert_company_access(user, company_id)
    assignment = await repo.get_assignment(company_id, assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return assignment


@router.patch(
    "/companies/{company_id}/assignments/{assignment_id}",
    response_model=AssignmentResponse,
)
async def update_assignment(
    company_id: int,
    assignment_id: int,
    payload: AssignmentUpdate,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Update a compliance check assignment (status, evidence, notes, review schedule)."""
    _assert_company_access(user, company_id)
    existing = await repo.get_assignment(company_id, assignment_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")

    updates = payload.model_dump(exclude_unset=True)
    updated = await repo.update_assignment(
        company_id,
        assignment_id,
        user_id=user.get("id"),
        **updates,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update assignment",
        )
    return updated


@router.delete(
    "/companies/{company_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_assignment(
    company_id: int,
    assignment_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
):
    """Remove a compliance check assignment (super admin only)."""
    existing = await repo.get_assignment(company_id, assignment_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await repo.delete_assignment(company_id, assignment_id)


# =============================================================================
# Evidence endpoints
# =============================================================================


@router.get(
    "/companies/{company_id}/assignments/{assignment_id}/evidence",
    response_model=list[EvidenceResponse],
)
async def list_evidence(
    company_id: int,
    assignment_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List evidence items for a compliance check assignment."""
    _assert_company_access(user, company_id)
    assignment = await repo.get_assignment(company_id, assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return await repo.list_evidence(assignment_id)


@router.post(
    "/companies/{company_id}/assignments/{assignment_id}/evidence",
    response_model=EvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_evidence(
    company_id: int,
    assignment_id: int,
    payload: EvidenceCreate,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Add an evidence item to a compliance check assignment."""
    _assert_company_access(user, company_id)
    assignment = await repo.get_assignment(company_id, assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return await repo.add_evidence(
        assignment_id=assignment_id,
        evidence_type=payload.evidence_type.value,
        title=payload.title,
        content=payload.content,
        file_path=payload.file_path,
        uploaded_by=user.get("id"),
    )


@router.delete(
    "/companies/{company_id}/assignments/{assignment_id}/evidence/{evidence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_evidence(
    company_id: int,
    assignment_id: int,
    evidence_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Delete an evidence item from a compliance check assignment."""
    _assert_company_access(user, company_id)
    assignment = await repo.get_assignment(company_id, assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await repo.delete_evidence(assignment_id, evidence_id)


# =============================================================================
# Audit endpoints
# =============================================================================


@router.get(
    "/companies/{company_id}/assignments/{assignment_id}/audit",
    response_model=list[AuditResponse],
)
async def list_audit(
    company_id: int,
    assignment_id: int,
    limit: int = 100,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List audit history for a compliance check assignment."""
    _assert_company_access(user, company_id)
    assignment = await repo.get_assignment(company_id, assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return await repo.list_audit(assignment_id, limit=limit)
