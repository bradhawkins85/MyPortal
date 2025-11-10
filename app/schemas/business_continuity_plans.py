from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PlanType(str, Enum):
    DISASTER_RECOVERY = "disaster_recovery"
    INCIDENT_RESPONSE = "incident_response"
    BUSINESS_CONTINUITY = "business_continuity"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PermissionLevel(str, Enum):
    READ = "read"
    EDIT = "edit"


class BusinessContinuityPlanBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    plan_type: PlanType
    content: str = Field(..., min_length=1)
    version: Optional[str] = Field(default="1.0", max_length=50)
    status: PlanStatus = PlanStatus.DRAFT


class BusinessContinuityPlanCreate(BusinessContinuityPlanBase):
    pass


class BusinessContinuityPlanUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    plan_type: Optional[PlanType] = None
    content: Optional[str] = Field(default=None, min_length=1)
    version: Optional[str] = Field(default=None, max_length=50)
    status: Optional[PlanStatus] = None
    last_reviewed_at: Optional[datetime] = None


class BusinessContinuityPlanResponse(BusinessContinuityPlanBase):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime
    last_reviewed_at: Optional[datetime] = None
    last_reviewed_by: Optional[int] = None

    class Config:
        from_attributes = True


class BusinessContinuityPlanListItem(BaseModel):
    id: int
    title: str
    plan_type: PlanType
    version: str
    status: PlanStatus
    created_at: datetime
    updated_at: datetime
    last_reviewed_at: Optional[datetime] = None
    created_by: int
    user_permission: Optional[PermissionLevel] = None

    class Config:
        from_attributes = True


class PlanPermissionBase(BaseModel):
    permission_level: PermissionLevel = PermissionLevel.READ


class PlanPermissionCreate(PlanPermissionBase):
    plan_id: int
    user_id: Optional[int] = None
    company_id: Optional[int] = None


class PlanPermissionUpdate(BaseModel):
    permission_level: Optional[PermissionLevel] = None


class PlanPermissionResponse(PlanPermissionBase):
    id: int
    plan_id: int
    user_id: Optional[int] = None
    company_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True
