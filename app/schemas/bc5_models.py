"""
Pydantic schemas for BC5 Business Continuity API endpoints.

These schemas provide validation and serialization for the BC5 RESTful API,
including templates, plans, versions, workflows, sections, attachments, and exports.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# Enums for BC5 API
class BCUserRole(str, Enum):
    """User roles for BC system RBAC."""
    
    VIEWER = "viewer"
    EDITOR = "editor"
    APPROVER = "approver"
    ADMIN = "admin"


class BCPlanListStatus(str, Enum):
    """Plan status options for listing/filtering."""
    
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ARCHIVED = "archived"


class BCVersionStatus(str, Enum):
    """Version status for plan versions."""
    
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class BCReviewDecision(str, Enum):
    """Review decision types."""
    
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class BCExportFormat(str, Enum):
    """Export format types."""
    
    DOCX = "docx"
    PDF = "pdf"


# Template Schemas
class BCTemplateListItem(BaseModel):
    """Schema for template list items."""
    
    id: int
    name: str
    version: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class BCTemplateDetail(BaseModel):
    """Schema for detailed template response."""
    
    id: int
    name: str
    version: str
    is_default: bool
    schema_json: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class BCTemplateCreate(BaseModel):
    """Schema for creating a new template."""
    
    name: str = Field(..., min_length=1, max_length=255, description="Template name")
    version: str = Field(..., max_length=50, description="Template version")
    is_default: bool = Field(default=False, description="Whether this is the default template")
    schema_json: Optional[dict[str, Any]] = Field(None, description="Template schema definition")


class BCTemplateUpdate(BaseModel):
    """Schema for updating a template."""
    
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Template name")
    version: Optional[str] = Field(None, max_length=50, description="Template version")
    is_default: Optional[bool] = Field(None, description="Whether this is the default template")
    schema_json: Optional[dict[str, Any]] = Field(None, description="Template schema definition")


# Plan Schemas
class BCPlanListItem(BaseModel):
    """Schema for plan list items."""
    
    id: int
    org_id: Optional[int] = None
    title: str
    status: str
    template_id: Optional[int] = None
    current_version_id: Optional[int] = None
    owner_user_id: int
    approved_at_utc: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    # Additional computed fields
    owner_name: Optional[str] = None
    template_name: Optional[str] = None
    current_version_number: Optional[int] = None
    
    class Config:
        from_attributes = True


class BCPlanDetail(BaseModel):
    """Schema for detailed plan response."""
    
    id: int
    org_id: Optional[int] = None
    title: str
    status: str
    template_id: Optional[int] = None
    current_version_id: Optional[int] = None
    owner_user_id: int
    approved_at_utc: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    # Related entities
    owner_name: Optional[str] = None
    template: Optional[BCTemplateDetail] = None
    current_version: Optional[dict[str, Any]] = None
    
    class Config:
        from_attributes = True


class BCPlanCreate(BaseModel):
    """Schema for creating a new plan."""
    
    org_id: Optional[int] = Field(None, description="Organization ID for multi-tenant support")
    title: str = Field(..., min_length=1, max_length=255, description="Plan title")
    status: BCPlanListStatus = Field(default=BCPlanListStatus.DRAFT, description="Plan status")
    template_id: Optional[int] = Field(None, description="Template ID to use for this plan")


class BCPlanUpdate(BaseModel):
    """Schema for updating a plan."""
    
    title: Optional[str] = Field(None, min_length=1, max_length=255, description="Plan title")
    status: Optional[BCPlanListStatus] = Field(None, description="Plan status")
    template_id: Optional[int] = Field(None, description="Template ID")
    owner_user_id: Optional[int] = Field(None, description="Owner user ID")


# Version Schemas
class BCVersionListItem(BaseModel):
    """Schema for version list items."""
    
    id: int
    plan_id: int
    version_number: int
    status: BCVersionStatus
    authored_by_user_id: int
    authored_at_utc: datetime
    summary_change_note: Optional[str] = None
    
    # Additional fields
    author_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class BCVersionDetail(BaseModel):
    """Schema for detailed version response."""
    
    id: int
    plan_id: int
    version_number: int
    status: BCVersionStatus
    authored_by_user_id: int
    authored_at_utc: datetime
    summary_change_note: Optional[str] = None
    content_json: Optional[dict[str, Any]] = None
    docx_export_hash: Optional[str] = None
    pdf_export_hash: Optional[str] = None
    
    # Additional fields
    author_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class BCVersionCreate(BaseModel):
    """Schema for creating a new version."""
    
    summary_change_note: Optional[str] = Field(None, description="Summary of changes in this version")
    content_json: Optional[dict[str, Any]] = Field(None, description="Version content as JSON")


class BCVersionActivate(BaseModel):
    """Schema for activating a version."""
    
    pass  # No body parameters needed, action performed via URL


# Review/Workflow Schemas
class BCReviewSubmit(BaseModel):
    """Schema for submitting a plan for review."""
    
    reviewer_user_ids: list[int] = Field(..., min_length=1, description="List of reviewer user IDs")
    notes: Optional[str] = Field(None, description="Notes for reviewers")


class BCReviewApprove(BaseModel):
    """Schema for approving a review."""
    
    notes: Optional[str] = Field(None, description="Approval notes")


class BCReviewRequestChanges(BaseModel):
    """Schema for requesting changes."""
    
    notes: str = Field(..., min_length=1, description="Required changes description")


class BCReviewListItem(BaseModel):
    """Schema for review list items."""
    
    id: int
    plan_id: int
    requested_by_user_id: int
    reviewer_user_id: int
    status: str
    requested_at_utc: datetime
    decided_at_utc: Optional[datetime] = None
    notes: Optional[str] = None
    
    # Additional fields
    requested_by_name: Optional[str] = None
    reviewer_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class BCAcknowledge(BaseModel):
    """Schema for acknowledging a plan."""
    
    ack_version_number: Optional[int] = Field(None, description="Version number being acknowledged")


class BCAcknowledgeResponse(BaseModel):
    """Schema for acknowledgment response."""
    
    id: int
    plan_id: int
    user_id: int
    ack_at_utc: datetime
    ack_version_number: Optional[int] = None
    
    class Config:
        from_attributes = True


# Section Schemas
class BCSectionListItem(BaseModel):
    """Schema for section list items."""
    
    key: str
    title: str
    order_index: int
    content: Optional[dict[str, Any]] = None
    
    class Config:
        from_attributes = True


class BCSectionUpdate(BaseModel):
    """Schema for updating a section."""
    
    content_json: dict[str, Any] = Field(..., description="Section content updates (partial)")
    
    @field_validator("content_json")
    @classmethod
    def validate_content_json(cls, v):
        """Ensure content_json is a valid dictionary."""
        if not isinstance(v, dict):
            raise ValueError("content_json must be a dictionary")
        return v


# Attachment Schemas
class BCAttachmentListItem(BaseModel):
    """Schema for attachment list items."""
    
    id: int
    plan_id: int
    file_name: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    uploaded_by_user_id: int
    uploaded_at_utc: datetime
    
    # Additional fields
    uploaded_by_name: Optional[str] = None
    download_url: Optional[str] = None
    
    class Config:
        from_attributes = True


class BCAttachmentUploadResponse(BaseModel):
    """Schema for file upload response."""
    
    id: int
    plan_id: int
    file_name: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    uploaded_by_user_id: int
    uploaded_at_utc: datetime
    storage_path: str
    hash: Optional[str] = None
    
    class Config:
        from_attributes = True


# Export Schemas
class BCExportRequest(BaseModel):
    """Schema for export request."""
    
    version_id: Optional[int] = Field(None, description="Specific version to export (defaults to current)")
    include_attachments: bool = Field(default=False, description="Include attachments in export")


class BCExportResponse(BaseModel):
    """Schema for export response."""
    
    export_url: str = Field(..., description="URL to download the export")
    format: BCExportFormat
    version_id: int
    generated_at: datetime
    expires_at: Optional[datetime] = None
    file_hash: Optional[str] = None
    
    class Config:
        from_attributes = True


# Audit & Change Log Schemas
class BCAuditListItem(BaseModel):
    """Schema for audit trail items."""
    
    id: int
    plan_id: int
    action: str
    actor_user_id: int
    details_json: Optional[dict[str, Any]] = None
    at_utc: datetime
    
    # Additional fields
    actor_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class BCChangeLogItem(BaseModel):
    """Schema for change log items."""
    
    id: int
    plan_id: int
    change_guid: str
    imported_at_utc: datetime
    
    # Change log details
    occurred_at: Optional[datetime] = None
    change_type: Optional[str] = None
    summary: Optional[str] = None
    
    class Config:
        from_attributes = True


# List/Filter Schemas
class BCPlanListFilters(BaseModel):
    """Schema for plan list filtering."""
    
    status: Optional[BCPlanListStatus] = Field(None, description="Filter by plan status")
    q: Optional[str] = Field(None, description="Search query for title/content")
    owner: Optional[int] = Field(None, description="Filter by owner user ID")
    template_id: Optional[int] = Field(None, description="Filter by template")
    org_id: Optional[int] = Field(None, description="Filter by organization")
    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=20, ge=1, le=100, description="Items per page")


class BCPaginatedResponse(BaseModel):
    """Generic paginated response schema."""
    
    items: list[Any]
    total: int
    page: int
    per_page: int
    total_pages: int
    
    class Config:
        from_attributes = True
