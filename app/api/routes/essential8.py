from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin, get_current_user
from app.api.dependencies.database import require_database
from app.repositories import essential8 as essential8_repo
from app.schemas.essential8 import (
    CompanyEssential8AuditResponse,
    CompanyEssential8ComplianceCreate,
    CompanyEssential8ComplianceResponse,
    CompanyEssential8ComplianceSummary,
    CompanyEssential8ComplianceUpdate,
    CompanyEssential8RequirementComplianceCreate,
    CompanyEssential8RequirementComplianceResponse,
    CompanyEssential8RequirementComplianceUpdate,
    ComplianceStatus,
    Essential8ControlResponse,
    Essential8ControlWithRequirementsResponse,
    Essential8RequirementResponse,
)

router = APIRouter(prefix="/api/essential8", tags=["Essential 8 Compliance"])


@router.get("/controls", response_model=list[Essential8ControlResponse])
async def list_controls(
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List all Essential 8 controls"""
    controls = await essential8_repo.list_essential8_controls()
    return controls


@router.get("/controls/{control_id}", response_model=Essential8ControlResponse)
async def get_control(
    control_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """Get a specific Essential 8 control"""
    control = await essential8_repo.get_essential8_control(control_id)
    if not control:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Control not found",
        )
    return control


@router.get(
    "/companies/{company_id}/compliance",
    response_model=list[CompanyEssential8ComplianceResponse],
)
async def list_company_compliance(
    company_id: int,
    status_filter: Optional[ComplianceStatus] = None,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List compliance records for a company"""
    # Super admins can view any company, regular users can only view their own
    is_super_admin = user.get("is_super_admin", False)
    user_company_id = user.get("company_id")
    
    if not is_super_admin and user_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view compliance for your own company",
        )
    
    records = await essential8_repo.list_company_compliance(
        company_id=company_id,
        status=status_filter,
    )
    return records


@router.get(
    "/companies/{company_id}/compliance/summary",
    response_model=CompanyEssential8ComplianceSummary,
)
async def get_company_compliance_summary(
    company_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Get a summary of compliance status for a company"""
    # Super admins can view any company, regular users can only view their own
    is_super_admin = user.get("is_super_admin", False)
    user_company_id = user.get("company_id")
    
    if not is_super_admin and user_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view compliance for your own company",
        )
    
    summary = await essential8_repo.get_company_compliance_summary(company_id)
    return summary


@router.post(
    "/companies/{company_id}/compliance/initialize",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def initialize_company_compliance(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Initialize compliance records for all Essential 8 controls for a company"""
    created_count = await essential8_repo.initialize_company_compliance(company_id)
    return {
        "message": "Compliance records initialized",
        "created_count": created_count,
    }


@router.post(
    "/companies/{company_id}/compliance",
    response_model=CompanyEssential8ComplianceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_compliance(
    company_id: int,
    payload: CompanyEssential8ComplianceCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Create a compliance record for a company"""
    # Ensure the company_id in the payload matches the URL
    if payload.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company ID in payload does not match URL",
        )
    
    # Check if record already exists
    existing = await essential8_repo.get_company_compliance(
        company_id=company_id,
        control_id=payload.control_id,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Compliance record already exists for this control",
        )
    
    record = await essential8_repo.create_company_compliance(
        **payload.model_dump(),
    )
    return record


@router.get(
    "/companies/{company_id}/compliance/{control_id}",
    response_model=CompanyEssential8ComplianceResponse,
)
async def get_company_compliance(
    company_id: int,
    control_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Get a specific compliance record for a company and control"""
    # Super admins can view any company, regular users can only view their own
    is_super_admin = user.get("is_super_admin", False)
    user_company_id = user.get("company_id")
    
    if not is_super_admin and user_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view compliance for your own company",
        )
    
    record = await essential8_repo.get_company_compliance(
        company_id=company_id,
        control_id=control_id,
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compliance record not found",
        )
    return record


@router.patch(
    "/companies/{company_id}/compliance/{control_id}",
    response_model=CompanyEssential8ComplianceResponse,
)
async def update_company_compliance(
    company_id: int,
    control_id: int,
    payload: CompanyEssential8ComplianceUpdate,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
):
    """Update a compliance record for a company"""
    # Check if record exists
    existing = await essential8_repo.get_company_compliance(
        company_id=company_id,
        control_id=control_id,
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compliance record not found",
        )
    
    # Update the record
    updates = payload.model_dump(exclude_unset=True)
    updated = await essential8_repo.update_company_compliance(
        company_id=company_id,
        control_id=control_id,
        user_id=user.get("id"),
        **updates,
    )
    
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update compliance record",
        )
    
    return updated


@router.get(
    "/companies/{company_id}/compliance/{control_id}/audit",
    response_model=list[CompanyEssential8AuditResponse],
)
async def list_compliance_audit(
    company_id: int,
    control_id: int,
    limit: int = 100,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List audit trail for compliance changes"""
    # Super admins can view any company, regular users can only view their own
    is_super_admin = user.get("is_super_admin", False)
    user_company_id = user.get("company_id")
    
    if not is_super_admin and user_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view audit trail for your own company",
        )
    
    audit = await essential8_repo.list_compliance_audit(
        company_id=company_id,
        control_id=control_id,
        limit=limit,
    )
    return audit


# =============================================================================
# Essential 8 Requirements Endpoints
# =============================================================================


@router.get("/requirements", response_model=list[Essential8RequirementResponse])
async def list_requirements(
    control_id: Optional[int] = None,
    maturity_level: Optional[str] = None,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List all Essential 8 requirements"""
    requirements = await essential8_repo.list_essential8_requirements(
        control_id=control_id,
        maturity_level=maturity_level,
    )
    return requirements


@router.get(
    "/controls/{control_id}/with-requirements",
    response_model=Essential8ControlWithRequirementsResponse,
)
async def get_control_with_requirements(
    control_id: int,
    company_id: Optional[int] = None,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Get a control with all its requirements grouped by maturity level"""
    # If company_id is provided, check permissions
    if company_id:
        is_super_admin = user.get("is_super_admin", False)
        user_company_id = user.get("company_id")
        
        if not is_super_admin and user_company_id != company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view compliance for your own company",
            )
    
    control_data = await essential8_repo.get_control_with_requirements(
        control_id=control_id,
        company_id=company_id,
    )
    
    if not control_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Control not found",
        )
    
    return control_data


@router.post(
    "/companies/{company_id}/requirements/initialize",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def initialize_company_requirement_compliance(
    company_id: int,
    control_id: Optional[int] = None,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Initialize requirement compliance records for a company"""
    created_count = await essential8_repo.initialize_company_requirement_compliance(
        company_id=company_id,
        control_id=control_id,
    )
    return {
        "message": "Requirement compliance records initialized",
        "created_count": created_count,
    }


@router.get(
    "/companies/{company_id}/requirements/compliance",
    response_model=list[CompanyEssential8RequirementComplianceResponse],
)
async def list_company_requirement_compliance(
    company_id: int,
    control_id: Optional[int] = None,
    maturity_level: Optional[str] = None,
    status_filter: Optional[ComplianceStatus] = None,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List requirement compliance records for a company"""
    # Permission check
    is_super_admin = user.get("is_super_admin", False)
    user_company_id = user.get("company_id")
    
    if not is_super_admin and user_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view compliance for your own company",
        )
    
    records = await essential8_repo.list_company_requirement_compliance(
        company_id=company_id,
        control_id=control_id,
        maturity_level=maturity_level,
        status=status_filter,
    )
    return records


@router.post(
    "/companies/{company_id}/requirements/compliance",
    response_model=CompanyEssential8RequirementComplianceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_requirement_compliance(
    company_id: int,
    payload: CompanyEssential8RequirementComplianceCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Create a requirement compliance record"""
    if payload.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company ID in payload does not match URL",
        )
    
    # Check if record already exists
    existing = await essential8_repo.get_company_requirement_compliance(
        company_id=company_id,
        requirement_id=payload.requirement_id,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Requirement compliance record already exists",
        )
    
    record = await essential8_repo.create_company_requirement_compliance(
        **payload.model_dump(),
    )
    return record


@router.get(
    "/companies/{company_id}/requirements/{requirement_id}/compliance",
    response_model=CompanyEssential8RequirementComplianceResponse,
)
async def get_company_requirement_compliance(
    company_id: int,
    requirement_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Get a specific requirement compliance record"""
    # Permission check
    is_super_admin = user.get("is_super_admin", False)
    user_company_id = user.get("company_id")
    
    if not is_super_admin and user_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view compliance for your own company",
        )
    
    record = await essential8_repo.get_company_requirement_compliance(
        company_id=company_id,
        requirement_id=requirement_id,
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requirement compliance record not found",
        )
    return record


@router.patch(
    "/companies/{company_id}/requirements/{requirement_id}/compliance",
    response_model=CompanyEssential8RequirementComplianceResponse,
)
async def update_company_requirement_compliance(
    company_id: int,
    requirement_id: int,
    payload: CompanyEssential8RequirementComplianceUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Update a requirement compliance record"""
    # Check if record exists
    existing = await essential8_repo.get_company_requirement_compliance(
        company_id=company_id,
        requirement_id=requirement_id,
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requirement compliance record not found",
        )
    
    # Get the requirement to find its control_id
    requirement = await essential8_repo.get_essential8_requirement(requirement_id)
    if not requirement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requirement not found",
        )
    
    # Update the record
    updates = payload.model_dump(exclude_unset=True)
    updated = await essential8_repo.update_company_requirement_compliance(
        company_id=company_id,
        requirement_id=requirement_id,
        **updates,
    )
    
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update requirement compliance record",
        )
    
    # Auto-update the control compliance based on requirement statuses
    await essential8_repo.auto_update_control_compliance_from_requirements(
        company_id=company_id,
        control_id=requirement["control_id"],
    )
    
    return updated
