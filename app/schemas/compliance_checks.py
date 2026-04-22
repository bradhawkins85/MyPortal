from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NOT_APPLICABLE = "not_applicable"


class EvidenceType(str, Enum):
    TEXT = "text"
    URL = "url"
    FILE = "file"


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class ComplianceCheckCategoryBase(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    is_system: bool = False


class ComplianceCheckCategoryCreate(ComplianceCheckCategoryBase):
    pass


class ComplianceCheckCategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None


class ComplianceCheckCategoryResponse(ComplianceCheckCategoryBase):
    id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Library checks
# ---------------------------------------------------------------------------


class ComplianceCheckBase(BaseModel):
    category_id: int
    code: str = Field(..., max_length=100)
    title: str = Field(..., max_length=255)
    description: Optional[str] = None
    guidance: Optional[str] = None
    default_review_interval_days: int = 365
    default_evidence_required: bool = False
    is_predefined: bool = False
    is_active: bool = True
    sort_order: int = 0


class ComplianceCheckCreate(ComplianceCheckBase):
    pass


class ComplianceCheckUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    guidance: Optional[str] = None
    default_review_interval_days: Optional[int] = None
    default_evidence_required: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ComplianceCheckResponse(ComplianceCheckBase):
    id: int
    category: Optional[ComplianceCheckCategoryResponse] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Assignments (per-company instances)
# ---------------------------------------------------------------------------


class AssignmentBase(BaseModel):
    status: CheckStatus = CheckStatus.NOT_STARTED
    review_interval_days: Optional[int] = None
    last_checked_at: Optional[datetime] = None
    notes: Optional[str] = None
    evidence_summary: Optional[str] = None
    owner_user_id: Optional[int] = None


class AssignmentCreate(BaseModel):
    check_id: int
    status: CheckStatus = CheckStatus.NOT_STARTED
    review_interval_days: Optional[int] = None
    notes: Optional[str] = None
    owner_user_id: Optional[int] = None


class BulkAssignByCategory(BaseModel):
    category_id: int


class AssignmentUpdate(BaseModel):
    status: Optional[CheckStatus] = None
    review_interval_days: Optional[int] = None
    last_checked_at: Optional[datetime] = None
    notes: Optional[str] = None
    evidence_summary: Optional[str] = None
    owner_user_id: Optional[int] = None
    archived: Optional[bool] = None


class AssignmentResponse(AssignmentBase):
    id: int
    company_id: int
    check_id: int
    check: Optional[ComplianceCheckResponse] = None
    last_checked_by: Optional[int] = None
    next_review_at: Optional[str] = None
    archived: bool = False
    is_overdue: bool = False
    is_due_soon: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class AssignmentSummary(BaseModel):
    company_id: int
    total: int = 0
    not_started: int = 0
    in_progress: int = 0
    compliant: int = 0
    non_compliant: int = 0
    not_applicable: int = 0
    overdue_count: int = 0
    due_soon_count: int = 0
    compliance_percentage: float = 0.0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceCreate(BaseModel):
    evidence_type: EvidenceType = EvidenceType.TEXT
    title: str = Field(..., max_length=255)
    content: Optional[str] = None
    file_path: Optional[str] = Field(None, max_length=500)


class EvidenceResponse(EvidenceCreate):
    id: int
    assignment_id: int
    uploaded_by: Optional[int] = None
    uploaded_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditResponse(BaseModel):
    id: int
    assignment_id: int
    company_id: int
    user_id: Optional[int] = None
    action: str
    old_status: Optional[CheckStatus] = None
    new_status: Optional[CheckStatus] = None
    old_last_checked_at: Optional[str] = None
    new_last_checked_at: Optional[str] = None
    change_summary: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
