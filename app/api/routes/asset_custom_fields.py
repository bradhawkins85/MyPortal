from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.repositories import asset_custom_fields as custom_fields_repo
from app.schemas.asset_custom_fields import (
    AssetFieldValue,
    FieldDefinition,
    FieldDefinitionCreate,
    FieldDefinitionUpdate,
    FieldValueSet,
)

router = APIRouter()


@router.get("/asset-custom-fields/definitions", response_model=list[FieldDefinition], tags=["Asset Custom Fields"])
async def list_field_definitions():
    """List all custom field definitions."""
    rows = await custom_fields_repo.list_field_definitions()
    return [FieldDefinition(**row) for row in rows]


@router.post("/asset-custom-fields/definitions", response_model=dict[str, Any], tags=["Asset Custom Fields"])
async def create_field_definition(definition: FieldDefinitionCreate):
    """Create a new custom field definition."""
    definition_id = await custom_fields_repo.create_field_definition(
        name=definition.name,
        field_type=definition.field_type.value,
        display_order=definition.display_order,
    )
    return {"id": definition_id, "message": "Field definition created successfully"}


@router.get("/asset-custom-fields/definitions/{definition_id}", response_model=FieldDefinition, tags=["Asset Custom Fields"])
async def get_field_definition(definition_id: int):
    """Get a single field definition."""
    row = await custom_fields_repo.get_field_definition(definition_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field definition not found")
    return FieldDefinition(**row)


@router.put("/asset-custom-fields/definitions/{definition_id}", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def update_field_definition(definition_id: int, definition: FieldDefinitionUpdate):
    """Update a custom field definition."""
    # Check if exists
    existing = await custom_fields_repo.get_field_definition(definition_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field definition not found")
    
    await custom_fields_repo.update_field_definition(
        definition_id=definition_id,
        name=definition.name,
        field_type=definition.field_type.value if definition.field_type else None,
        display_order=definition.display_order,
    )
    return {"message": "Field definition updated successfully"}


@router.delete("/asset-custom-fields/definitions/{definition_id}", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def delete_field_definition(definition_id: int):
    """Delete a custom field definition."""
    # Check if exists
    existing = await custom_fields_repo.get_field_definition(definition_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field definition not found")
    
    await custom_fields_repo.delete_field_definition(definition_id)
    return {"message": "Field definition deleted successfully"}


@router.get("/assets/{asset_id}/custom-fields", response_model=list[AssetFieldValue], tags=["Asset Custom Fields"])
async def get_asset_custom_fields(asset_id: int):
    """Get all custom field values for an asset."""
    rows = await custom_fields_repo.get_asset_field_values(asset_id)
    return [AssetFieldValue.from_db_row(row) for row in rows]


@router.post("/assets/{asset_id}/custom-fields", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def set_asset_custom_fields(asset_id: int, fields: list[FieldValueSet]):
    """Set custom field values for an asset."""
    from app.schemas.asset_custom_fields import FieldType
    
    for field in fields:
        # Get field definition to determine type
        definition = await custom_fields_repo.get_field_definition(field.field_definition_id)
        if not definition:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Field definition {field.field_definition_id} not found"
            )
        
        field_type = FieldType(definition["field_type"])
        
        # Set appropriate value based on field type
        value_text = None
        value_date = None
        value_boolean = None
        
        if field.value is None:
            # Clear the value
            pass
        elif field_type == FieldType.CHECKBOX:
            value_boolean = bool(field.value)
        elif field_type == FieldType.DATE:
            value_date = str(field.value) if field.value else None
        else:  # text, image, url
            value_text = str(field.value) if field.value else None
        
        await custom_fields_repo.set_asset_field_value(
            asset_id=asset_id,
            field_definition_id=field.field_definition_id,
            value_text=value_text,
            value_date=value_date,
            value_boolean=value_boolean,
        )
    
    return {"message": "Custom fields updated successfully"}


@router.delete("/assets/{asset_id}/custom-fields/{field_definition_id}", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def delete_asset_custom_field(asset_id: int, field_definition_id: int):
    """Delete a custom field value for an asset."""
    await custom_fields_repo.delete_asset_field_value(asset_id, field_definition_id)
    return {"message": "Custom field value deleted successfully"}
