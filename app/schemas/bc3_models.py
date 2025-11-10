"""
Pydantic schemas for BC3 Business Continuity Planning models.

These schemas provide validation and serialization for the BCP data model.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# Enums
class BCPlanStatus(str, Enum):
    """Status lifecycle for business continuity plans."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ARCHIVED = "archived"


class BCPlanVersionStatus(str, Enum):
    """Status for plan versions."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"


class BCReviewStatus(str, Enum):
    """Status for review workflow."""

    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


# BC Plan Schemas
class BCPlanBase(BaseModel):
    """Base schema for BC Plan."""

    org_id: Optional[int] = Field(None, description="Organization ID for multi-tenant support")
    title: str = Field(..., min_length=1, max_length=255)
    status: BCPlanStatus = Field(default=BCPlanStatus.DRAFT)
    template_id: Optional[int] = None
    owner_user_id: int


class BCPlanCreate(BCPlanBase):
    """Schema for creating a BC Plan."""

    pass


class BCPlanUpdate(BaseModel):
    """Schema for updating a BC Plan."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[BCPlanStatus] = None
    template_id: Optional[int] = None
    current_version_id: Optional[int] = None
    owner_user_id: Optional[int] = None
    approved_at_utc: Optional[datetime] = None


class BCPlanResponse(BCPlanBase):
    """Schema for BC Plan response."""

    id: int
    current_version_id: Optional[int] = None
    approved_at_utc: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Plan Version Schemas
class BCPlanVersionBase(BaseModel):
    """Base schema for BC Plan Version."""

    plan_id: int
    version_number: int = Field(..., gt=0)
    status: BCPlanVersionStatus = Field(default=BCPlanVersionStatus.ACTIVE)
    authored_by_user_id: int
    summary_change_note: Optional[str] = None
    content_json: Optional[dict[str, Any]] = Field(None, description="Section data as JSON")


class BCPlanVersionCreate(BCPlanVersionBase):
    """Schema for creating a BC Plan Version."""

    pass


class BCPlanVersionUpdate(BaseModel):
    """Schema for updating a BC Plan Version."""

    status: Optional[BCPlanVersionStatus] = None
    summary_change_note: Optional[str] = None
    content_json: Optional[dict[str, Any]] = None
    docx_export_hash: Optional[str] = Field(None, max_length=64)
    pdf_export_hash: Optional[str] = Field(None, max_length=64)


class BCPlanVersionResponse(BCPlanVersionBase):
    """Schema for BC Plan Version response."""

    id: int
    authored_at_utc: datetime
    docx_export_hash: Optional[str] = None
    pdf_export_hash: Optional[str] = None

    class Config:
        from_attributes = True


# BC Template Schemas
class BCTemplateBase(BaseModel):
    """Base schema for BC Template."""

    name: str = Field(..., min_length=1, max_length=255)
    version: str = Field(..., max_length=50)
    is_default: bool = Field(default=False)
    schema_json: Optional[dict[str, Any]] = Field(None, description="Section and field definitions")


class BCTemplateCreate(BCTemplateBase):
    """Schema for creating a BC Template."""

    pass


class BCTemplateUpdate(BaseModel):
    """Schema for updating a BC Template."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    version: Optional[str] = Field(None, max_length=50)
    is_default: Optional[bool] = None
    schema_json: Optional[dict[str, Any]] = None


class BCTemplateResponse(BCTemplateBase):
    """Schema for BC Template response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Section Definition Schemas
class BCSectionDefinitionBase(BaseModel):
    """Base schema for BC Section Definition."""

    template_id: int
    key: str = Field(..., min_length=1, max_length=100, description="Unique section key")
    title: str = Field(..., min_length=1, max_length=255)
    order_index: int = Field(default=0)
    schema_json: Optional[dict[str, Any]] = Field(None, description="Field definitions")


class BCSectionDefinitionCreate(BCSectionDefinitionBase):
    """Schema for creating a BC Section Definition."""

    pass


class BCSectionDefinitionUpdate(BaseModel):
    """Schema for updating a BC Section Definition."""

    key: Optional[str] = Field(None, min_length=1, max_length=100)
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    order_index: Optional[int] = None
    schema_json: Optional[dict[str, Any]] = None


class BCSectionDefinitionResponse(BCSectionDefinitionBase):
    """Schema for BC Section Definition response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Contact Schemas
class BCContactBase(BaseModel):
    """Base schema for BC Contact."""

    plan_id: int
    name: str = Field(..., min_length=1, max_length=255)
    role: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class BCContactCreate(BCContactBase):
    """Schema for creating a BC Contact."""

    pass


class BCContactUpdate(BaseModel):
    """Schema for updating a BC Contact."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    role: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class BCContactResponse(BCContactBase):
    """Schema for BC Contact response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Process Schemas
class BCProcessBase(BaseModel):
    """Base schema for BC Process."""

    plan_id: int
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    rto_minutes: Optional[int] = Field(None, ge=0, description="Recovery Time Objective")
    rpo_minutes: Optional[int] = Field(None, ge=0, description="Recovery Point Objective")
    mtpd_minutes: Optional[int] = Field(None, ge=0, description="Maximum Tolerable Period of Disruption")
    impact_rating: Optional[str] = Field(None, max_length=50)
    dependencies_json: Optional[dict[str, Any]] = Field(None, description="Process dependencies")


class BCProcessCreate(BCProcessBase):
    """Schema for creating a BC Process."""

    pass


class BCProcessUpdate(BaseModel):
    """Schema for updating a BC Process."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    rto_minutes: Optional[int] = Field(None, ge=0)
    rpo_minutes: Optional[int] = Field(None, ge=0)
    mtpd_minutes: Optional[int] = Field(None, ge=0)
    impact_rating: Optional[str] = Field(None, max_length=50)
    dependencies_json: Optional[dict[str, Any]] = None


class BCProcessResponse(BCProcessBase):
    """Schema for BC Process response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Risk Schemas
class BCRiskBase(BaseModel):
    """Base schema for BC Risk."""

    plan_id: int
    threat: str = Field(..., min_length=1, max_length=500)
    likelihood: Optional[str] = Field(None, max_length=50)
    impact: Optional[str] = Field(None, max_length=50)
    rating: Optional[str] = Field(None, max_length=50)
    mitigation: Optional[str] = None
    owner_user_id: Optional[int] = Field(None, description="Risk owner")


class BCRiskCreate(BCRiskBase):
    """Schema for creating a BC Risk."""

    pass


class BCRiskUpdate(BaseModel):
    """Schema for updating a BC Risk."""

    threat: Optional[str] = Field(None, min_length=1, max_length=500)
    likelihood: Optional[str] = Field(None, max_length=50)
    impact: Optional[str] = Field(None, max_length=50)
    rating: Optional[str] = Field(None, max_length=50)
    mitigation: Optional[str] = None
    owner_user_id: Optional[int] = None


class BCRiskResponse(BCRiskBase):
    """Schema for BC Risk response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Attachment Schemas
class BCAttachmentBase(BaseModel):
    """Base schema for BC Attachment."""

    plan_id: int
    file_name: str = Field(..., min_length=1, max_length=255)
    storage_path: str = Field(..., min_length=1, max_length=500)
    content_type: Optional[str] = Field(None, max_length=100)
    size_bytes: Optional[int] = Field(None, ge=0)
    uploaded_by_user_id: int


class BCAttachmentCreate(BCAttachmentBase):
    """Schema for creating a BC Attachment."""

    pass


class BCAttachmentUpdate(BaseModel):
    """Schema for updating a BC Attachment."""

    file_name: Optional[str] = Field(None, min_length=1, max_length=255)
    storage_path: Optional[str] = Field(None, min_length=1, max_length=500)
    content_type: Optional[str] = Field(None, max_length=100)
    size_bytes: Optional[int] = Field(None, ge=0)
    hash: Optional[str] = Field(None, max_length=64)


class BCAttachmentResponse(BCAttachmentBase):
    """Schema for BC Attachment response."""

    id: int
    uploaded_at_utc: datetime
    hash: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Review Schemas
class BCReviewBase(BaseModel):
    """Base schema for BC Review."""

    plan_id: int
    requested_by_user_id: int
    reviewer_user_id: int
    status: BCReviewStatus = Field(default=BCReviewStatus.PENDING)
    notes: Optional[str] = None


class BCReviewCreate(BCReviewBase):
    """Schema for creating a BC Review."""

    pass


class BCReviewUpdate(BaseModel):
    """Schema for updating a BC Review."""

    status: Optional[BCReviewStatus] = None
    decided_at_utc: Optional[datetime] = None
    notes: Optional[str] = None


class BCReviewResponse(BCReviewBase):
    """Schema for BC Review response."""

    id: int
    requested_at_utc: datetime
    decided_at_utc: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# BC Acknowledgment Schemas
class BCAckBase(BaseModel):
    """Base schema for BC Acknowledgment."""

    plan_id: int
    user_id: int
    ack_version_number: Optional[int] = Field(None, description="Version acknowledged")


class BCAckCreate(BCAckBase):
    """Schema for creating a BC Acknowledgment."""

    pass


class BCAckResponse(BCAckBase):
    """Schema for BC Acknowledgment response."""

    id: int
    ack_at_utc: datetime

    class Config:
        from_attributes = True


# BC Audit Schemas
class BCAuditBase(BaseModel):
    """Base schema for BC Audit."""

    plan_id: int
    action: str = Field(..., min_length=1, max_length=100)
    actor_user_id: int
    details_json: Optional[dict[str, Any]] = Field(None, description="Additional audit details")


class BCAuditCreate(BCAuditBase):
    """Schema for creating a BC Audit entry."""

    pass


class BCAuditResponse(BCAuditBase):
    """Schema for BC Audit response."""

    id: int
    at_utc: datetime

    class Config:
        from_attributes = True


# BC Change Log Map Schemas
class BCChangeLogMapBase(BaseModel):
    """Base schema for BC Change Log Map."""

    plan_id: int
    change_guid: str = Field(..., min_length=36, max_length=36, description="Change log GUID")


class BCChangeLogMapCreate(BCChangeLogMapBase):
    """Schema for creating a BC Change Log Map."""

    pass


class BCChangeLogMapResponse(BCChangeLogMapBase):
    """Schema for BC Change Log Map response."""

    id: int
    imported_at_utc: datetime

    class Config:
        from_attributes = True


# BC Vendor Schemas
class BCVendorBase(BaseModel):
    """Base schema for BC Vendor."""

    plan_id: int
    name: str = Field(..., min_length=1, max_length=255)
    vendor_type: Optional[str] = Field(None, max_length=100, description="e.g., IT Service Provider, Supplier, Cloud Provider")
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    sla_notes: Optional[str] = Field(None, description="Service Level Agreement details and notes")
    contract_reference: Optional[str] = Field(None, max_length=255, description="Contract number or reference")
    criticality: Optional[str] = Field(None, max_length=50, description="e.g., critical, high, medium, low")


class BCVendorCreate(BCVendorBase):
    """Schema for creating a BC Vendor."""

    pass


class BCVendorUpdate(BaseModel):
    """Schema for updating a BC Vendor."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    vendor_type: Optional[str] = Field(None, max_length=100)
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    sla_notes: Optional[str] = None
    contract_reference: Optional[str] = Field(None, max_length=255)
    criticality: Optional[str] = Field(None, max_length=50)


class BCVendorResponse(BCVendorBase):
    """Schema for BC Vendor response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
