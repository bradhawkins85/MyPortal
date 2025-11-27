from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class FieldType(str, Enum):
    """Supported field types for BCP template fields."""
    TEXT = "text"
    RICH_TEXT = "rich_text"
    DATE = "date"
    DATETIME = "datetime"
    SELECT = "select"
    MULTISELECT = "multiselect"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    TABLE = "table"
    FILE = "file"
    CONTACT_REF = "contact_ref"
    USER_REF = "user_ref"
    URL = "url"


class FieldChoice(BaseModel):
    """Choice option for select/multiselect fields."""
    value: str = Field(..., description="The value to be stored")
    label: str = Field(..., description="The display label for the choice")


class TableColumn(BaseModel):
    """Column definition for table fields."""
    column_id: str = Field(..., description="Unique identifier for the column")
    label: str = Field(..., description="Display label for the column")
    field_type: FieldType = Field(..., description="Data type for this column")
    required: bool = Field(default=False, description="Whether this column is required")
    help_text: Optional[str] = Field(default=None, description="Help text for this column")
    choices: Optional[list[FieldChoice]] = Field(default=None, description="Choices for select/multiselect columns")
    min_value: Optional[float] = Field(default=None, description="Minimum value for numeric columns")
    max_value: Optional[float] = Field(default=None, description="Maximum value for numeric columns")
    is_computed: bool = Field(default=False, description="Whether this column is computed or cross-referenced")


class TemplateField(BaseModel):
    """Field definition within a BCP template section."""
    field_id: str = Field(..., description="Unique identifier for the field")
    label: str = Field(..., description="Display label for the field")
    field_type: FieldType = Field(..., description="Data type for this field")
    required: bool = Field(default=False, description="Whether this field is required")
    help_text: Optional[str] = Field(default=None, description="Help text describing the field")
    choices: Optional[list[FieldChoice]] = Field(default=None, description="Choices for select/multiselect fields")
    min_value: Optional[float] = Field(default=None, description="Minimum value for numeric fields")
    max_value: Optional[float] = Field(default=None, description="Maximum value for numeric fields")
    default_value: Optional[Any] = Field(default=None, description="Default value for the field")
    columns: Optional[list[TableColumn]] = Field(default=None, description="Column definitions for table fields")
    is_computed: bool = Field(default=False, description="Whether this field is computed or cross-referenced")
    computation_note: Optional[str] = Field(default=None, description="Description of how computed fields are calculated")


class TemplateSection(BaseModel):
    """Section definition within a BCP template."""
    section_id: str = Field(..., description="Unique identifier for the section")
    title: str = Field(..., description="Display title for the section")
    description: Optional[str] = Field(default=None, description="Description of this section's purpose")
    order: int = Field(..., description="Display order within parent section or template")
    parent_section_id: Optional[str] = Field(default=None, description="ID of parent section for nested sections")
    fields: list[TemplateField] = Field(default_factory=list, description="Fields contained in this section")


class TemplateMetadata(BaseModel):
    """Metadata about the BCP template."""
    template_name: str = Field(..., description="Name of the template")
    template_version: str = Field(..., description="Version of the template")
    description: Optional[str] = Field(default=None, description="Description of the template")
    requires_approval: bool = Field(default=True, description="Whether plans using this template require approval")
    approval_workflow: Optional[str] = Field(default=None, description="Description of the approval workflow")
    revision_tracking: bool = Field(default=True, description="Whether revision history should be tracked")
    attachments_required: list[str] = Field(default_factory=list, description="List of required attachments")


class BCPTemplateSchema(BaseModel):
    """Complete BCP template schema definition."""
    metadata: TemplateMetadata = Field(..., description="Template metadata")
    sections: list[TemplateSection] = Field(..., description="All sections in the template")
    
    class Config:
        json_schema_extra = {
            "example": {
                "metadata": {
                    "template_name": "Government Business Continuity Plan",
                    "template_version": "1.0",
                    "description": "Standard BCP template for government organizations",
                    "requires_approval": True,
                    "approval_workflow": "Plans require approval from business unit head and risk management",
                    "revision_tracking": True,
                    "attachments_required": ["contact_list", "vendor_slas", "site_information"]
                },
                "sections": [
                    {
                        "section_id": "plan_overview",
                        "title": "Plan Overview",
                        "description": "Overview of the business continuity plan",
                        "order": 1,
                        "parent_section_id": None,
                        "fields": [
                            {
                                "field_id": "purpose",
                                "label": "Purpose",
                                "field_type": "rich_text",
                                "required": True,
                                "help_text": "Describe the purpose of this business continuity plan"
                            }
                        ]
                    }
                ]
            }
        }
