from __future__ import annotations
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, validator

from app.services import issues as issues_service


class IssueAssignmentBase(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default=issues_service.DEFAULT_STATUS, max_length=32)

    @validator("company_name")
    def validate_company_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Company name is required")
        return cleaned

    @validator("status")
    def validate_status(cls, value: str) -> str:
        try:
            return issues_service.normalise_status(value)
        except ValueError as exc:  # pragma: no cover - validation handles message
            raise ValueError("Invalid status") from exc


class IssueAssignmentCreate(IssueAssignmentBase):
    pass


class IssueAssignmentResponse(BaseModel):
    assignment_id: Optional[int]
    issue_id: int
    company_id: Optional[int]
    company_name: Optional[str]
    status: str
    status_label: str
    updated_at: Optional[datetime]
    updated_at_iso: Optional[str]


class IssueBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)

    @validator("name")
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Issue name is required")
        return cleaned

    @validator("slug")
    def validate_slug(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @validator("description")
    def validate_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class IssueCreate(IssueBase):
    companies: List[IssueAssignmentCreate] = Field(default_factory=list)


class IssueUpdate(BaseModel):
    description: Optional[str] = Field(default=None, max_length=2000)
    new_name: Optional[str] = Field(default=None, max_length=255)
    new_slug: Optional[str] = Field(default=None, max_length=255)
    add_companies: List[IssueAssignmentCreate] = Field(default_factory=list)

    @validator("description")
    def validate_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @validator("new_name")
    def validate_new_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("New issue name cannot be empty")
        return cleaned

    @validator("new_slug")
    def validate_new_slug(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class IssueResponse(BaseModel):
    name: str
    slug: Optional[str]
    description: Optional[str]
    created_at: Optional[datetime]
    created_at_iso: Optional[str]
    updated_at: Optional[datetime]
    updated_at_iso: Optional[str]
    assignments: List[IssueAssignmentResponse]


class IssueListResponse(BaseModel):
    items: List[IssueResponse]
    total: int


class IssueStatusUpdate(BaseModel):
    issue_name: str = Field(..., min_length=1, max_length=255)
    company_name: str = Field(..., min_length=1, max_length=255)
    status: str = Field(..., max_length=32)

    @validator("issue_name", "company_name")
    def validate_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field is required")
        return cleaned

    @validator("status")
    def validate_status(cls, value: str) -> str:
        try:
            return issues_service.normalise_status(value)
        except ValueError as exc:
            raise ValueError("Invalid status") from exc


class IssueStatusResponse(BaseModel):
    issue_name: str
    company_name: Optional[str]
    status: str
    status_label: str
    updated_at: Optional[datetime]
    updated_at_iso: Optional[str]
