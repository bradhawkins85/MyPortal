from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.repositories import assets as assets_repo
from app.repositories import asset_custom_fields as custom_fields_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import user_companies as user_company_repo
from app.services import tickets as tickets_service
from app.schemas.asset_custom_fields import (
    AssetFieldValue,
    FieldDefinition,
    FieldDefinitionCreate,
    FieldDefinitionUpdate,
    FieldValueSet,
)

router = APIRouter()


async def _has_helpdesk_permission(current_user: dict) -> bool:
    if current_user.get("is_super_admin"):
        return True
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        return False
    try:
        return await membership_repo.user_has_permission(
            user_id, tickets_service.HELPDESK_PERMISSION_KEY
        )
    except RuntimeError:
        return False


async def _require_asset_custom_field_access(asset_id: int, current_user: dict) -> dict[str, Any]:
    """Authorise access to an asset's custom field values.

    Custom field values are only shown on staff (asset write) views, so the
    caller must be a super admin, a helpdesk technician, or hold asset write
    access (``menu.assets`` write / ``can_manage_assets``) through an active
    membership in the asset's company.
    """

    asset = await assets_repo.get_asset_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if await _has_helpdesk_permission(current_user):
        return asset
    try:
        user_id = int(current_user.get("id"))
        company_id = int(asset.get("company_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found") from None
    membership = await user_company_repo.get_user_company(user_id, company_id)
    if membership:
        from app import main as main_module

        if main_module._membership_menu_can(current_user, membership, "menu.assets", write=True):
            return asset
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")


@router.get("/asset-custom-fields/definitions", response_model=list[FieldDefinition], tags=["Asset Custom Fields"])
async def list_field_definitions(_: dict = Depends(get_current_user)):
    """List all custom field definitions."""
    rows = await custom_fields_repo.list_field_definitions()
    return [FieldDefinition(**row) for row in rows]


@router.post("/asset-custom-fields/definitions", response_model=dict[str, Any], tags=["Asset Custom Fields"])
async def create_field_definition(
    definition: FieldDefinitionCreate, _: dict = Depends(require_super_admin)
):
    """Create a new custom field definition."""
    definition_id = await custom_fields_repo.create_field_definition(
        name=definition.name,
        field_type=definition.field_type.value,
        display_order=definition.display_order,
        display_name=definition.display_name or None,
    )
    return {"id": definition_id, "message": "Field definition created successfully"}


@router.get("/asset-custom-fields/definitions/{definition_id}", response_model=FieldDefinition, tags=["Asset Custom Fields"])
async def get_field_definition(definition_id: int, _: dict = Depends(get_current_user)):
    """Get a single field definition."""
    row = await custom_fields_repo.get_field_definition(definition_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field definition not found")
    return FieldDefinition(**row)


@router.put("/asset-custom-fields/definitions/{definition_id}", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def update_field_definition(
    definition_id: int,
    definition: FieldDefinitionUpdate,
    _: dict = Depends(require_super_admin),
):
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
        display_name=definition.display_name,
    )
    return {"message": "Field definition updated successfully"}


@router.delete("/asset-custom-fields/definitions/{definition_id}", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def delete_field_definition(definition_id: int, _: dict = Depends(require_super_admin)):
    """Delete a custom field definition."""
    # Check if exists
    existing = await custom_fields_repo.get_field_definition(definition_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field definition not found")
    
    await custom_fields_repo.delete_field_definition(definition_id)
    return {"message": "Field definition deleted successfully"}


@router.get("/assets/{asset_id}/custom-fields", response_model=list[AssetFieldValue], tags=["Asset Custom Fields"])
async def get_asset_custom_fields(asset_id: int, current_user: dict = Depends(get_current_user)):
    """Get all custom field values for an asset."""
    await _require_asset_custom_field_access(asset_id, current_user)
    rows = await custom_fields_repo.get_asset_field_values(asset_id)
    return [AssetFieldValue.from_db_row(row) for row in rows]


@router.post("/assets/{asset_id}/custom-fields", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def set_asset_custom_fields(
    asset_id: int,
    fields: list[FieldValueSet],
    send_tray_notification: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Set custom field values for an asset."""
    asset = await _require_asset_custom_field_access(asset_id, current_user)
    from app.schemas.asset_custom_fields import FieldType
    from app.services import tray as tray_service

    for field in fields:
        definition = await custom_fields_repo.get_field_definition(field.field_definition_id)
        if not definition:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Field definition {field.field_definition_id} not found"
            )

        field_type = FieldType(definition["field_type"])

        value_text = None
        value_date = None
        value_boolean = None

        if field.value is None:
            pass
        elif field_type == FieldType.CHECKBOX:
            value_boolean = bool(field.value)
        elif field_type == FieldType.DATE:
            value_date = str(field.value) if field.value else None
        else:
            value_text = str(field.value) if field.value else None

        await custom_fields_repo.set_asset_field_value(
            asset_id=asset_id,
            field_definition_id=field.field_definition_id,
            value_text=value_text,
            value_date=value_date,
            value_boolean=value_boolean,
        )

    if send_tray_notification:
        if asset and asset.get("company_id"):
            asset_name = str(asset.get("name") or f"Asset #{asset_id}").strip()
            await tray_service.push_notification_to_company_devices(
                company_id=int(asset["company_id"]),
                title="Asset updated",
                body=f"{asset_name} has been updated.",
                asset_ids=[asset_id],
            )

    return {"message": "Custom fields updated successfully"}


@router.delete("/assets/{asset_id}/custom-fields/{field_definition_id}", response_model=dict[str, str], tags=["Asset Custom Fields"])
async def delete_asset_custom_field(
    asset_id: int, field_definition_id: int, current_user: dict = Depends(get_current_user)
):
    """Delete a custom field value for an asset."""
    await _require_asset_custom_field_access(asset_id, current_user)
    await custom_fields_repo.delete_asset_field_value(asset_id, field_definition_id)
    return {"message": "Custom field value deleted successfully"}
