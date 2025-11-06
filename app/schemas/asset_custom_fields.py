from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FieldType(str, Enum):
    """Custom field types."""
    TEXT = "text"
    IMAGE = "image"
    CHECKBOX = "checkbox"
    URL = "url"
    DATE = "date"


class FieldDefinitionCreate(BaseModel):
    """Schema for creating a custom field definition."""
    name: str = Field(..., min_length=1, max_length=255)
    field_type: FieldType
    display_order: int = Field(default=0, ge=0)


class FieldDefinitionUpdate(BaseModel):
    """Schema for updating a custom field definition."""
    name: str | None = Field(None, min_length=1, max_length=255)
    field_type: FieldType | None = None
    display_order: int | None = Field(None, ge=0)


class FieldDefinition(BaseModel):
    """Schema for a custom field definition."""
    id: int
    name: str
    field_type: FieldType
    display_order: int
    created_at: datetime
    updated_at: datetime


class FieldValueSet(BaseModel):
    """Schema for setting a custom field value."""
    field_definition_id: int
    value: str | bool | date | None = None

    @field_validator('value', mode='before')
    @classmethod
    def validate_value(cls, v: Any) -> Any:
        """Convert value to appropriate type."""
        if v is None or v == "":
            return None
        return v


class AssetFieldValue(BaseModel):
    """Schema for an asset custom field value."""
    id: int
    asset_id: int
    field_definition_id: int
    field_name: str
    field_type: FieldType
    display_order: int
    value: str | bool | date | None = None

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> AssetFieldValue:
        """Create instance from database row."""
        field_type = FieldType(row["field_type"])
        
        # Determine the value based on field type
        if field_type == FieldType.CHECKBOX:
            value = row.get("value_boolean")
        elif field_type == FieldType.DATE:
            value = row.get("value_date")
        else:  # text, image, url
            value = row.get("value_text")
        
        return cls(
            id=row["id"],
            asset_id=row["asset_id"],
            field_definition_id=row["field_definition_id"],
            field_name=row["field_name"],
            field_type=field_type,
            display_order=row["display_order"],
            value=value,
        )
