"""
Pydantic schemas for BCP (Business Continuity Planning) BC02 models.

These schemas provide validation and serialization for all BCP entities.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# Enums
# ============================================================================


class BcpPriority(str, Enum):
    """Priority levels for critical activities."""

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class BcpSupplierDependency(str, Enum):
    """Supplier dependency levels."""

    NONE = "None"
    SOLE = "Sole"
    MAJOR = "Major"
    MANY = "Many"


class BcpIncidentStatus(str, Enum):
    """Incident status."""

    ACTIVE = "Active"
    CLOSED = "Closed"


class BcpIncidentSource(str, Enum):
    """Source of incident trigger."""

    MANUAL = "Manual"
    UPTIMEKUMA = "UptimeKuma"
    OTHER = "Other"


class BcpPhase(str, Enum):
    """Response phases."""

    IMMEDIATE = "Immediate"
    CRISIS_RECOVERY = "CrisisRecovery"


class BcpKitCategory(str, Enum):
    """Emergency kit item categories."""

    DOCUMENT = "Document"
    EQUIPMENT = "Equipment"


class BcpContactKind(str, Enum):
    """Contact types."""

    INTERNAL = "Internal"
    EXTERNAL = "External"


# ============================================================================
# Core Entity Schemas
# ============================================================================


class BcpPlanBase(BaseModel):
    """Base schema for BCP Plan."""

    company_id: int = Field(..., gt=0, description="Company ID for multi-tenancy")
    title: str = Field(..., min_length=1, max_length=255)
    executive_summary: Optional[str] = None
    objectives: Optional[str] = None
    version: Optional[str] = Field(None, max_length=50)
    last_reviewed_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    distribution_notes: Optional[str] = None


class BcpPlanCreate(BcpPlanBase):
    """Schema for creating a BCP Plan."""

    pass


class BcpPlanUpdate(BaseModel):
    """Schema for updating a BCP Plan."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    executive_summary: Optional[str] = None
    objectives: Optional[str] = None
    version: Optional[str] = Field(None, max_length=50)
    last_reviewed_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    distribution_notes: Optional[str] = None


class BcpPlanResponse(BcpPlanBase):
    """Schema for BCP Plan response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpDistributionEntryBase(BaseModel):
    """Base schema for Distribution Entry."""

    plan_id: int = Field(..., gt=0)
    copy_number: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=255)
    location: Optional[str] = Field(None, max_length=255)


class BcpDistributionEntryCreate(BcpDistributionEntryBase):
    """Schema for creating a Distribution Entry."""

    pass


class BcpDistributionEntryUpdate(BaseModel):
    """Schema for updating a Distribution Entry."""

    copy_number: Optional[int] = Field(None, gt=0)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    location: Optional[str] = Field(None, max_length=255)


class BcpDistributionEntryResponse(BcpDistributionEntryBase):
    """Schema for Distribution Entry response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# Risk & Preparedness Schemas
# ============================================================================


class BcpRiskBase(BaseModel):
    """Base schema for Risk."""

    plan_id: int = Field(..., gt=0)
    description: str = Field(..., min_length=1)
    likelihood: Optional[int] = Field(None, ge=1, le=4, description="Likelihood rating 1-4")
    impact: Optional[int] = Field(None, ge=1, le=4, description="Impact rating 1-4")
    rating: Optional[int] = Field(None, description="Computed risk rating")
    severity: Optional[str] = Field(None, max_length=50, description="Computed severity")
    preventative_actions: Optional[str] = None
    contingency_plans: Optional[str] = None


class BcpRiskCreate(BcpRiskBase):
    """Schema for creating a Risk."""

    pass


class BcpRiskUpdate(BaseModel):
    """Schema for updating a Risk."""

    description: Optional[str] = Field(None, min_length=1)
    likelihood: Optional[int] = Field(None, ge=1, le=4)
    impact: Optional[int] = Field(None, ge=1, le=4)
    rating: Optional[int] = None
    severity: Optional[str] = Field(None, max_length=50)
    preventative_actions: Optional[str] = None
    contingency_plans: Optional[str] = None


class BcpRiskResponse(BcpRiskBase):
    """Schema for Risk response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpInsurancePolicyBase(BaseModel):
    """Base schema for Insurance Policy."""

    plan_id: int = Field(..., gt=0)
    type: str = Field(..., min_length=1, max_length=100)
    coverage: Optional[str] = None
    exclusions: Optional[str] = None
    insurer: Optional[str] = Field(None, max_length=255)
    contact: Optional[str] = Field(None, max_length=255)
    last_review_date: Optional[datetime] = None
    payment_terms: Optional[str] = Field(None, max_length=255)


class BcpInsurancePolicyCreate(BcpInsurancePolicyBase):
    """Schema for creating an Insurance Policy."""

    pass


class BcpInsurancePolicyUpdate(BaseModel):
    """Schema for updating an Insurance Policy."""

    type: Optional[str] = Field(None, min_length=1, max_length=100)
    coverage: Optional[str] = None
    exclusions: Optional[str] = None
    insurer: Optional[str] = Field(None, max_length=255)
    contact: Optional[str] = Field(None, max_length=255)
    last_review_date: Optional[datetime] = None
    payment_terms: Optional[str] = Field(None, max_length=255)


class BcpInsurancePolicyResponse(BcpInsurancePolicyBase):
    """Schema for Insurance Policy response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpBackupItemBase(BaseModel):
    """Base schema for Backup Item."""

    plan_id: int = Field(..., gt=0)
    data_scope: str = Field(..., min_length=1, max_length=255)
    frequency: Optional[str] = Field(None, max_length=100)
    medium: Optional[str] = Field(None, max_length=100)
    owner: Optional[str] = Field(None, max_length=255)
    steps: Optional[str] = None


class BcpBackupItemCreate(BcpBackupItemBase):
    """Schema for creating a Backup Item."""

    pass


class BcpBackupItemUpdate(BaseModel):
    """Schema for updating a Backup Item."""

    data_scope: Optional[str] = Field(None, min_length=1, max_length=255)
    frequency: Optional[str] = Field(None, max_length=100)
    medium: Optional[str] = Field(None, max_length=100)
    owner: Optional[str] = Field(None, max_length=255)
    steps: Optional[str] = None


class BcpBackupItemResponse(BcpBackupItemBase):
    """Schema for Backup Item response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# Business Impact Analysis Schemas
# ============================================================================


class BcpCriticalActivityBase(BaseModel):
    """Base schema for Critical Activity."""

    plan_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    priority: Optional[BcpPriority] = None
    supplier_dependency: Optional[BcpSupplierDependency] = None
    notes: Optional[str] = None


class BcpCriticalActivityCreate(BcpCriticalActivityBase):
    """Schema for creating a Critical Activity."""

    pass


class BcpCriticalActivityUpdate(BaseModel):
    """Schema for updating a Critical Activity."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    priority: Optional[BcpPriority] = None
    supplier_dependency: Optional[BcpSupplierDependency] = None
    notes: Optional[str] = None


class BcpCriticalActivityResponse(BcpCriticalActivityBase):
    """Schema for Critical Activity response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpImpactBase(BaseModel):
    """Base schema for Impact."""

    critical_activity_id: int = Field(..., gt=0)
    losses_financial: Optional[str] = None
    losses_staffing: Optional[str] = None
    losses_reputation: Optional[str] = None
    fines: Optional[str] = None
    legal_liability: Optional[str] = None
    rto_hours: Optional[int] = Field(None, ge=0, description="RTO in hours")


class BcpImpactCreate(BcpImpactBase):
    """Schema for creating an Impact."""

    pass


class BcpImpactUpdate(BaseModel):
    """Schema for updating an Impact."""

    losses_financial: Optional[str] = None
    losses_staffing: Optional[str] = None
    losses_reputation: Optional[str] = None
    fines: Optional[str] = None
    legal_liability: Optional[str] = None
    rto_hours: Optional[int] = Field(None, ge=0)


class BcpImpactResponse(BcpImpactBase):
    """Schema for Impact response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# Incident Response Schemas
# ============================================================================


class BcpIncidentBase(BaseModel):
    """Base schema for Incident."""

    plan_id: int = Field(..., gt=0)
    started_at: datetime
    status: BcpIncidentStatus = Field(default=BcpIncidentStatus.ACTIVE)
    source: Optional[BcpIncidentSource] = None


class BcpIncidentCreate(BcpIncidentBase):
    """Schema for creating an Incident."""

    pass


class BcpIncidentUpdate(BaseModel):
    """Schema for updating an Incident."""

    started_at: Optional[datetime] = None
    status: Optional[BcpIncidentStatus] = None
    source: Optional[BcpIncidentSource] = None


class BcpIncidentResponse(BcpIncidentBase):
    """Schema for Incident response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpChecklistItemBase(BaseModel):
    """Base schema for Checklist Item."""

    plan_id: int = Field(..., gt=0)
    phase: BcpPhase
    label: str = Field(..., min_length=1, max_length=500)
    default_order: int = Field(default=0)


class BcpChecklistItemCreate(BcpChecklistItemBase):
    """Schema for creating a Checklist Item."""

    pass


class BcpChecklistItemUpdate(BaseModel):
    """Schema for updating a Checklist Item."""

    phase: Optional[BcpPhase] = None
    label: Optional[str] = Field(None, min_length=1, max_length=500)
    default_order: Optional[int] = None


class BcpChecklistItemResponse(BcpChecklistItemBase):
    """Schema for Checklist Item response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpChecklistTickBase(BaseModel):
    """Base schema for Checklist Tick."""

    plan_id: int = Field(..., gt=0)
    checklist_item_id: int = Field(..., gt=0)
    incident_id: int = Field(..., gt=0)
    is_done: bool = Field(default=False)
    done_at: Optional[datetime] = None
    done_by: Optional[int] = None


class BcpChecklistTickCreate(BcpChecklistTickBase):
    """Schema for creating a Checklist Tick."""

    pass


class BcpChecklistTickUpdate(BaseModel):
    """Schema for updating a Checklist Tick."""

    is_done: Optional[bool] = None
    done_at: Optional[datetime] = None
    done_by: Optional[int] = None


class BcpChecklistTickResponse(BcpChecklistTickBase):
    """Schema for Checklist Tick response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpEvacuationPlanBase(BaseModel):
    """Base schema for Evacuation Plan."""

    plan_id: int = Field(..., gt=0)
    meeting_point: Optional[str] = Field(None, max_length=500)
    floorplan_file_id: Optional[int] = None
    notes: Optional[str] = None


class BcpEvacuationPlanCreate(BcpEvacuationPlanBase):
    """Schema for creating an Evacuation Plan."""

    pass


class BcpEvacuationPlanUpdate(BaseModel):
    """Schema for updating an Evacuation Plan."""

    meeting_point: Optional[str] = Field(None, max_length=500)
    floorplan_file_id: Optional[int] = None
    notes: Optional[str] = None


class BcpEvacuationPlanResponse(BcpEvacuationPlanBase):
    """Schema for Evacuation Plan response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpEmergencyKitItemBase(BaseModel):
    """Base schema for Emergency Kit Item."""

    plan_id: int = Field(..., gt=0)
    category: BcpKitCategory
    name: str = Field(..., min_length=1, max_length=255)
    notes: Optional[str] = None
    last_checked_at: Optional[datetime] = None


class BcpEmergencyKitItemCreate(BcpEmergencyKitItemBase):
    """Schema for creating an Emergency Kit Item."""

    pass


class BcpEmergencyKitItemUpdate(BaseModel):
    """Schema for updating an Emergency Kit Item."""

    category: Optional[BcpKitCategory] = None
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    notes: Optional[str] = None
    last_checked_at: Optional[datetime] = None


class BcpEmergencyKitItemResponse(BcpEmergencyKitItemBase):
    """Schema for Emergency Kit Item response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpRoleBase(BaseModel):
    """Base schema for Role."""

    plan_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=1, max_length=255)
    responsibilities: Optional[str] = None


class BcpRoleCreate(BcpRoleBase):
    """Schema for creating a Role."""

    pass


class BcpRoleUpdate(BaseModel):
    """Schema for updating a Role."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    responsibilities: Optional[str] = None


class BcpRoleResponse(BcpRoleBase):
    """Schema for Role response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpRoleAssignmentBase(BaseModel):
    """Base schema for Role Assignment."""

    role_id: int = Field(..., gt=0)
    user_id: int = Field(..., gt=0)
    is_alternate: bool = Field(default=False)
    contact_info: Optional[str] = Field(None, max_length=500)


class BcpRoleAssignmentCreate(BcpRoleAssignmentBase):
    """Schema for creating a Role Assignment."""

    pass


class BcpRoleAssignmentUpdate(BaseModel):
    """Schema for updating a Role Assignment."""

    user_id: Optional[int] = Field(None, gt=0)
    is_alternate: Optional[bool] = None
    contact_info: Optional[str] = Field(None, max_length=500)


class BcpRoleAssignmentResponse(BcpRoleAssignmentBase):
    """Schema for Role Assignment response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpContactBase(BaseModel):
    """Base schema for Contact."""

    plan_id: int = Field(..., gt=0)
    kind: BcpContactKind
    person_or_org: str = Field(..., min_length=1, max_length=255)
    phones: Optional[str] = Field(None, max_length=500)
    email: Optional[str] = Field(None, max_length=255)
    responsibility_or_agency: Optional[str] = Field(None, max_length=500)


class BcpContactCreate(BcpContactBase):
    """Schema for creating a Contact."""

    pass


class BcpContactUpdate(BaseModel):
    """Schema for updating a Contact."""

    kind: Optional[BcpContactKind] = None
    person_or_org: Optional[str] = Field(None, min_length=1, max_length=255)
    phones: Optional[str] = Field(None, max_length=500)
    email: Optional[str] = Field(None, max_length=255)
    responsibility_or_agency: Optional[str] = Field(None, max_length=500)


class BcpContactResponse(BcpContactBase):
    """Schema for Contact response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpEventLogEntryBase(BaseModel):
    """Base schema for Event Log Entry."""

    plan_id: int = Field(..., gt=0)
    incident_id: Optional[int] = None
    happened_at: datetime
    author_id: Optional[int] = None
    notes: str = Field(..., min_length=1)
    initials: Optional[str] = Field(None, max_length=10)


class BcpEventLogEntryCreate(BcpEventLogEntryBase):
    """Schema for creating an Event Log Entry."""

    pass


class BcpEventLogEntryUpdate(BaseModel):
    """Schema for updating an Event Log Entry."""

    incident_id: Optional[int] = None
    happened_at: Optional[datetime] = None
    author_id: Optional[int] = None
    notes: Optional[str] = Field(None, min_length=1)
    initials: Optional[str] = Field(None, max_length=10)


class BcpEventLogEntryResponse(BcpEventLogEntryBase):
    """Schema for Event Log Entry response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# Recovery Schemas
# ============================================================================


class BcpRecoveryActionBase(BaseModel):
    """Base schema for Recovery Action."""

    plan_id: int = Field(..., gt=0)
    critical_activity_id: Optional[int] = None
    action: str = Field(..., min_length=1)
    resources: Optional[str] = None
    owner_id: Optional[int] = None
    rto_hours: Optional[int] = Field(None, ge=0)
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class BcpRecoveryActionCreate(BcpRecoveryActionBase):
    """Schema for creating a Recovery Action."""

    pass


class BcpRecoveryActionUpdate(BaseModel):
    """Schema for updating a Recovery Action."""

    critical_activity_id: Optional[int] = None
    action: Optional[str] = Field(None, min_length=1)
    resources: Optional[str] = None
    owner_id: Optional[int] = None
    rto_hours: Optional[int] = Field(None, ge=0)
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class BcpRecoveryActionResponse(BcpRecoveryActionBase):
    """Schema for Recovery Action response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpRecoveryContactBase(BaseModel):
    """Base schema for Recovery Contact."""

    plan_id: int = Field(..., gt=0)
    org_name: str = Field(..., min_length=1, max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    title: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


class BcpRecoveryContactCreate(BcpRecoveryContactBase):
    """Schema for creating a Recovery Contact."""

    pass


class BcpRecoveryContactUpdate(BaseModel):
    """Schema for updating a Recovery Contact."""

    org_name: Optional[str] = Field(None, min_length=1, max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    title: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


class BcpRecoveryContactResponse(BcpRecoveryContactBase):
    """Schema for Recovery Contact response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpInsuranceClaimBase(BaseModel):
    """Base schema for Insurance Claim."""

    plan_id: int = Field(..., gt=0)
    insurer: str = Field(..., min_length=1, max_length=255)
    claim_date: Optional[datetime] = None
    details: Optional[str] = None
    follow_up_actions: Optional[str] = None


class BcpInsuranceClaimCreate(BcpInsuranceClaimBase):
    """Schema for creating an Insurance Claim."""

    pass


class BcpInsuranceClaimUpdate(BaseModel):
    """Schema for updating an Insurance Claim."""

    insurer: Optional[str] = Field(None, min_length=1, max_length=255)
    claim_date: Optional[datetime] = None
    details: Optional[str] = None
    follow_up_actions: Optional[str] = None


class BcpInsuranceClaimResponse(BcpInsuranceClaimBase):
    """Schema for Insurance Claim response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpMarketChangeBase(BaseModel):
    """Base schema for Market Change."""

    plan_id: int = Field(..., gt=0)
    change: str = Field(..., min_length=1)
    impact: Optional[str] = None
    options: Optional[str] = None


class BcpMarketChangeCreate(BcpMarketChangeBase):
    """Schema for creating a Market Change."""

    pass


class BcpMarketChangeUpdate(BaseModel):
    """Schema for updating a Market Change."""

    change: Optional[str] = Field(None, min_length=1)
    impact: Optional[str] = None
    options: Optional[str] = None


class BcpMarketChangeResponse(BcpMarketChangeBase):
    """Schema for Market Change response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpTrainingItemBase(BaseModel):
    """Base schema for Training Item."""

    plan_id: int = Field(..., gt=0)
    training_date: datetime
    training_type: Optional[str] = Field(None, max_length=255)
    comments: Optional[str] = None


class BcpTrainingItemCreate(BcpTrainingItemBase):
    """Schema for creating a Training Item."""

    pass


class BcpTrainingItemUpdate(BaseModel):
    """Schema for updating a Training Item."""

    training_date: Optional[datetime] = None
    training_type: Optional[str] = Field(None, max_length=255)
    comments: Optional[str] = None


class BcpTrainingItemResponse(BcpTrainingItemBase):
    """Schema for Training Item response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BcpReviewItemBase(BaseModel):
    """Base schema for Review Item."""

    plan_id: int = Field(..., gt=0)
    review_date: datetime
    reason: Optional[str] = None
    changes_made: Optional[str] = None


class BcpReviewItemCreate(BcpReviewItemBase):
    """Schema for creating a Review Item."""

    pass


class BcpReviewItemUpdate(BaseModel):
    """Schema for updating a Review Item."""

    review_date: Optional[datetime] = None
    reason: Optional[str] = None
    changes_made: Optional[str] = None


class BcpReviewItemResponse(BcpReviewItemBase):
    """Schema for Review Item response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
