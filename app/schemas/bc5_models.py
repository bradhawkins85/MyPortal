"""
Pydantic schemas for BC5 Business Continuity API endpoints.

These schemas provide validation and serialization for the BC5 RESTful API,
including templates, plans, versions, workflows, sections, attachments, and exports.
"""
from __future__ import annotations

from datetime import datetime, timezone
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


# ============================================================================
# Additional BC6 Schemas
# ============================================================================

# Template Structure Schemas (aliases for clarity)
class FieldSchema(BaseModel):
    """Schema for a field definition within a template section.
    
    This schema defines the structure and validation rules for individual fields
    within a business continuity plan template.
    """
    
    field_id: str = Field(..., min_length=1, max_length=100, description="Unique identifier for the field")
    label: str = Field(..., min_length=1, max_length=255, description="Display label for the field")
    field_type: str = Field(..., description="Data type for this field (text, date, select, etc.)")
    required: bool = Field(default=False, description="Whether this field is required")
    help_text: Optional[str] = Field(None, description="Help text describing the field")
    default_value: Optional[Any] = Field(None, description="Default value for the field")
    validation_rules: Optional[dict[str, Any]] = Field(None, description="Additional validation rules")
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "field_id": "rto_minutes",
                "label": "Recovery Time Objective (minutes)",
                "field_type": "integer",
                "required": True,
                "help_text": "Target time to recover this process",
                "validation_rules": {"min": 0, "max": 525600}
            }
        }


class SectionSchema(BaseModel):
    """Schema for a section definition within a template.
    
    Sections organize related fields within a business continuity plan template.
    """
    
    section_id: str = Field(..., min_length=1, max_length=100, description="Unique identifier for the section")
    title: str = Field(..., min_length=1, max_length=255, description="Display title for the section")
    description: Optional[str] = Field(None, description="Description of this section's purpose")
    order: int = Field(..., ge=0, description="Display order within parent section or template")
    parent_section_id: Optional[str] = Field(None, description="ID of parent section for nested sections")
    fields: list[FieldSchema] = Field(default_factory=list, description="Fields contained in this section")
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "section_id": "recovery_objectives",
                "title": "Recovery Objectives",
                "description": "Define RTO, RPO, and MTPD for critical processes",
                "order": 1,
                "parent_section_id": None,
                "fields": []
            }
        }


class TemplateSchema(BaseModel):
    """Complete template schema definition.
    
    This schema represents the full structure of a business continuity plan template,
    including all sections and field definitions.
    """
    
    template_id: Optional[int] = Field(None, description="Template ID (for existing templates)")
    name: str = Field(..., min_length=1, max_length=255, description="Template name")
    version: str = Field(..., max_length=50, description="Template version")
    description: Optional[str] = Field(None, description="Template description")
    is_default: bool = Field(default=False, description="Whether this is the default template")
    sections: list[SectionSchema] = Field(default_factory=list, description="All sections in the template")
    metadata: Optional[dict[str, Any]] = Field(None, description="Additional template metadata")
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "name": "Government Business Continuity Plan",
                "version": "2.0",
                "description": "Standard BCP template for government organizations",
                "is_default": True,
                "sections": []
            }
        }


# Review and Workflow Schemas
class ReviewCreate(BaseModel):
    """Schema for creating a review request.
    
    Used to submit a plan for review to specified reviewers.
    """
    
    plan_id: int = Field(..., gt=0, description="ID of the plan to review")
    reviewer_user_ids: list[int] = Field(..., min_length=1, description="List of reviewer user IDs")
    notes: Optional[str] = Field(None, max_length=2000, description="Notes for reviewers")
    due_date: Optional[datetime] = Field(None, description="Review due date (ISO 8601 UTC)")
    
    @field_validator("due_date")
    @classmethod
    def validate_due_date_utc(cls, v):
        """Ensure due date is in UTC."""
        if v is not None and v.tzinfo is None:
            raise ValueError("due_date must include timezone information (ISO 8601 UTC)")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "plan_id": 1,
                "reviewer_user_ids": [2, 3, 4],
                "notes": "Please review the updated recovery procedures",
                "due_date": "2024-12-31T23:59:59+00:00"
            }
        }


class ReviewDecision(BaseModel):
    """Schema for recording a review decision.
    
    Used when a reviewer approves or requests changes to a plan.
    """
    
    review_id: int = Field(..., gt=0, description="ID of the review")
    decision: BCReviewDecision = Field(..., description="Review decision (approved or changes_requested)")
    notes: str = Field(..., min_length=1, max_length=5000, description="Review notes")
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Decision timestamp (ISO 8601 UTC)")
    
    @field_validator("decided_at")
    @classmethod
    def validate_decided_at_utc(cls, v):
        """Ensure decision timestamp is in UTC."""
        if v.tzinfo is None:
            raise ValueError("decided_at must include timezone information (ISO 8601 UTC)")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "review_id": 1,
                "decision": "approved",
                "notes": "All recovery procedures are clear and well-documented",
                "decided_at": "2024-01-15T10:30:00+00:00"
            }
        }


# Attachment Schemas
class AttachmentMeta(BaseModel):
    """Metadata schema for plan attachments.
    
    Contains essential information about an attached file without the file content itself.
    """
    
    id: Optional[int] = Field(None, description="Attachment ID (for existing attachments)")
    plan_id: int = Field(..., gt=0, description="ID of the plan this attachment belongs to")
    file_name: str = Field(..., min_length=1, max_length=255, description="Original file name")
    content_type: Optional[str] = Field(None, max_length=100, description="MIME type of the file")
    size_bytes: Optional[int] = Field(None, ge=0, description="File size in bytes")
    uploaded_by_user_id: Optional[int] = Field(None, description="ID of user who uploaded the file")
    uploaded_at_utc: Optional[datetime] = Field(None, description="Upload timestamp (ISO 8601 UTC)")
    hash: Optional[str] = Field(None, max_length=64, description="SHA256 hash for integrity verification")
    storage_path: Optional[str] = Field(None, max_length=500, description="Path in storage system")
    tags: Optional[list[str]] = Field(default_factory=list, description="Tags for categorizing attachments")
    
    @field_validator("uploaded_at_utc")
    @classmethod
    def validate_uploaded_at_utc(cls, v):
        """Ensure upload timestamp is in UTC."""
        if v is not None and v.tzinfo is None:
            raise ValueError("uploaded_at_utc must include timezone information (ISO 8601 UTC)")
        return v
    
    @field_validator("size_bytes")
    @classmethod
    def validate_size_bytes(cls, v):
        """Ensure file size is non-negative."""
        if v is not None and v < 0:
            raise ValueError("size_bytes must be non-negative")
        return v
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "plan_id": 1,
                "file_name": "contact_list.pdf",
                "content_type": "application/pdf",
                "size_bytes": 245760,
                "hash": "a1b2c3d4e5f6...",
                "tags": ["contacts", "emergency"]
            }
        }


# Process and Recovery Objective Schemas
class BCProcessBase(BaseModel):
    """Base schema for business continuity processes with recovery objectives.
    
    Includes validation for RTO, RPO, and MTPD to ensure they are non-negative integers.
    """
    
    plan_id: int = Field(..., gt=0, description="ID of the plan this process belongs to")
    name: str = Field(..., min_length=1, max_length=255, description="Process name")
    description: Optional[str] = Field(None, description="Process description")
    rto_minutes: Optional[int] = Field(None, ge=0, description="Recovery Time Objective in minutes (non-negative)")
    rpo_minutes: Optional[int] = Field(None, ge=0, description="Recovery Point Objective in minutes (non-negative)")
    mtpd_minutes: Optional[int] = Field(None, ge=0, description="Maximum Tolerable Period of Disruption in minutes (non-negative)")
    impact_rating: Optional[str] = Field(None, max_length=50, description="Impact rating (critical, high, medium, low)")
    dependencies_json: Optional[dict[str, Any]] = Field(None, description="Process dependencies as JSON")
    
    @field_validator("rto_minutes", "rpo_minutes", "mtpd_minutes")
    @classmethod
    def validate_non_negative(cls, v, info):
        """Ensure recovery objectives are non-negative integers."""
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} must be a non-negative integer")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "plan_id": 1,
                "name": "Email Service",
                "description": "Corporate email system",
                "rto_minutes": 240,
                "rpo_minutes": 60,
                "mtpd_minutes": 480,
                "impact_rating": "critical"
            }
        }


class BCProcessCreate(BCProcessBase):
    """Schema for creating a business continuity process."""
    pass


class BCProcessUpdate(BaseModel):
    """Schema for updating a business continuity process.
    
    All fields are optional to support partial updates.
    """
    
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    rto_minutes: Optional[int] = Field(None, ge=0, description="Recovery Time Objective in minutes (non-negative)")
    rpo_minutes: Optional[int] = Field(None, ge=0, description="Recovery Point Objective in minutes (non-negative)")
    mtpd_minutes: Optional[int] = Field(None, ge=0, description="Maximum Tolerable Period of Disruption in minutes (non-negative)")
    impact_rating: Optional[str] = Field(None, max_length=50)
    dependencies_json: Optional[dict[str, Any]] = None
    
    @field_validator("rto_minutes", "rpo_minutes", "mtpd_minutes")
    @classmethod
    def validate_non_negative(cls, v, info):
        """Ensure recovery objectives are non-negative integers."""
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} must be a non-negative integer")
        return v


class BCProcessDetail(BCProcessBase):
    """Detailed schema for business continuity process response."""
    
    id: int
    created_at: datetime
    updated_at: datetime
    
    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_datetime_utc(cls, v):
        """Ensure timestamps are in UTC."""
        if v.tzinfo is None:
            raise ValueError("Timestamps must include timezone information (ISO 8601 UTC)")
        return v
    
    class Config:
        from_attributes = True


# Status Transition Validation
class PlanStatusTransition(BaseModel):
    """Schema for validating plan status transitions.
    
    Ensures status changes follow allowed transitions:
    - draft -> in_review
    - in_review -> approved or draft
    - approved -> archived
    - archived -> draft (to reactivate)
    """
    
    current_status: BCPlanListStatus = Field(..., description="Current plan status")
    new_status: BCPlanListStatus = Field(..., description="Desired new status")
    
    @field_validator("new_status")
    @classmethod
    def validate_status_transition(cls, v, info):
        """Validate that the status transition is allowed."""
        if "current_status" not in info.data:
            return v
        
        current = info.data["current_status"]
        new = v
        
        # Define allowed transitions
        allowed_transitions = {
            BCPlanListStatus.DRAFT: [BCPlanListStatus.IN_REVIEW],
            BCPlanListStatus.IN_REVIEW: [BCPlanListStatus.APPROVED, BCPlanListStatus.DRAFT],
            BCPlanListStatus.APPROVED: [BCPlanListStatus.ARCHIVED],
            BCPlanListStatus.ARCHIVED: [BCPlanListStatus.DRAFT],
        }
        
        # Check if transition is allowed
        if current == new:
            return v  # Same status is always allowed
        
        if new not in allowed_transitions.get(current, []):
            raise ValueError(
                f"Invalid status transition from {current.value} to {new.value}. "
                f"Allowed transitions from {current.value}: "
                f"{[s.value for s in allowed_transitions.get(current, [])]}"
            )
        
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "current_status": "draft",
                "new_status": "in_review"
            }
        }


# Alias exports for backward compatibility and convenience
PlanCreate = BCPlanCreate
PlanUpdate = BCPlanUpdate
PlanDetail = BCPlanDetail
PlanVersionCreate = BCVersionCreate
PlanVersionDetail = BCVersionDetail
ExportRequest = BCExportRequest
