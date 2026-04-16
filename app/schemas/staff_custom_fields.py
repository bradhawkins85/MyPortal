from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FieldType(str, Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    DATE = "date"
    SELECT = "select"
    MULTISELECT = "multiselect"


class FieldDefinition(BaseModel):
    id: int
    name: str
    display_name: str | None = None
    field_type: FieldType
    display_order: int
    company_id: int | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class FieldDefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    display_name: str | None = Field(None, max_length=255)
    field_type: FieldType
    display_order: int = Field(default=0, ge=0)
    options: list[dict[str, str]] = Field(default_factory=list)


class FieldDefinitionUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    field_type: FieldType
    display_order: int = Field(default=0, ge=0)
    is_active: bool = True
    options: list[dict[str, str]] = Field(default_factory=list)


class FieldValueSet(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    value: str | bool | date | None = None

    @field_validator("value", mode="before")
    @classmethod
    def normalise_value(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


class StaffCustomFieldValue(BaseModel):
    staff_id: int
    name: str
    field_type: FieldType
    value: str | bool | date | None = None
    updated_at: datetime | None = None
