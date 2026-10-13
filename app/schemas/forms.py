"""Pydantic schemas for the Custom Forms feature."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FormCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1024)


class FormUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1024)
    active: Optional[bool] = None


class FormResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: Optional[str]
    active: bool

    class Config:
        from_attributes = True


class FormFieldBase(BaseModel):
    field_type: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=255)
    placeholder: Optional[str] = Field(default=None, max_length=255)
    help_text: Optional[str] = Field(default=None, max_length=2000)
    required: bool = False
    options: Optional[list[str]] = Field(default=None, max_items=100)
    default_value: Optional[str] = Field(default=None, max_length=255)


class FormFieldCreate(FormFieldBase):
    pass


class FormFieldUpdate(BaseModel):
    field_type: Optional[str] = Field(default=None, min_length=1, max_length=40)
    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    placeholder: Optional[str] = Field(default=None, max_length=255)
    help_text: Optional[str] = Field(default=None, max_length=2000)
    required: Optional[bool] = None
    options: Optional[list[str]] = Field(default=None, max_items=100)
    default_value: Optional[str] = Field(default=None, max_length=255)


class FormFieldResponse(FormFieldBase):
    id: int
    form_id: int
    sort_order: int

    class Config:
        from_attributes = True


class FormDetailResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: Optional[str]
    active: bool
    fields: list[FormFieldResponse] = Field(default_factory=list)


class FieldReorder(BaseModel):
    field_ids: list[int] = Field(..., min_length=1)


class FormAccessUpdate(BaseModel):
    user_ids: list[int] = Field(default_factory=list)


class SubmissionResponse(BaseModel):
    id: int
    form_id: int
    ticket_id: Optional[int]
    data: Optional[dict[str, Any]]
    ip_address: Optional[str]
    user_agent: Optional[str]
    submitted_by: Optional[int]
    created_at: Optional[str]