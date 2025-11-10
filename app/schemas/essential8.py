from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ComplianceStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"


class MaturityLevel(str, Enum):
    ML0 = "ml0"  # Not implemented
    ML1 = "ml1"  # Maturity Level 1
    ML2 = "ml2"  # Maturity Level 2
    ML3 = "ml3"  # Maturity Level 3


class Essential8ControlResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    control_order: int

    class Config:
        from_attributes = True


class CompanyEssential8ComplianceBase(BaseModel):
    status: ComplianceStatus = ComplianceStatus.NOT_STARTED
    maturity_level: MaturityLevel = MaturityLevel.ML0
    evidence: Optional[str] = None
    notes: Optional[str] = None
    last_reviewed_date: Optional[date] = None
    target_compliance_date: Optional[date] = None


class CompanyEssential8ComplianceCreate(CompanyEssential8ComplianceBase):
    company_id: int
    control_id: int


class CompanyEssential8ComplianceUpdate(BaseModel):
    status: Optional[ComplianceStatus] = None
    maturity_level: Optional[MaturityLevel] = None
    evidence: Optional[str] = None
    notes: Optional[str] = None
    last_reviewed_date: Optional[date] = None
    target_compliance_date: Optional[date] = None


class CompanyEssential8ComplianceResponse(CompanyEssential8ComplianceBase):
    id: int
    company_id: int
    control_id: int
    control: Optional[Essential8ControlResponse] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class CompanyEssential8ComplianceSummary(BaseModel):
    """Summary of compliance status for a company"""
    company_id: int
    total_controls: int = 8
    not_started: int = 0
    in_progress: int = 0
    compliant: int = 0
    non_compliant: int = 0
    compliance_percentage: float = 0.0
    average_maturity_level: float = 0.0


class CompanyEssential8AuditResponse(BaseModel):
    id: int
    compliance_id: int
    company_id: int
    control_id: int
    user_id: Optional[int] = None
    action: str
    old_status: Optional[ComplianceStatus] = None
    new_status: Optional[ComplianceStatus] = None
    old_maturity_level: Optional[MaturityLevel] = None
    new_maturity_level: Optional[MaturityLevel] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
