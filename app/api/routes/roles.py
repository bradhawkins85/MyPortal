from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import roles as role_repo
from app.schemas.roles import RoleCreate, RoleResponse, RoleUpdate
from app.services import audit as audit_service

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    return await role_repo.list_roles()


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    existing = await role_repo.get_role_by_name(payload.name)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role name already exists")
    created = await role_repo.create_role(
        name=payload.name,
        description=payload.description,
        permissions=payload.permissions,
        is_system=payload.is_system,
    )
    await audit_service.log_action(
        action="role.created",
        user_id=current_user["id"],
        entity_type="role",
        entity_id=created["id"],
        new_value=created,
        request=request,
    )
    return created


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    role = await role_repo.get_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role


@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    role = await role_repo.get_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if role.get("is_system") and payload.name and payload.name != role["name"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="System roles cannot be renamed",
        )
    data = payload.model_dump(exclude_unset=True)
    updated = await role_repo.update_role(role_id, **data)
    await audit_service.log_action(
        action="role.updated",
        user_id=current_user["id"],
        entity_type="role",
        entity_id=role_id,
        previous_value=role,
        new_value=updated,
        request=request,
    )
    return updated


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    role = await role_repo.get_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if role.get("is_system"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete system role")
    await role_repo.delete_role(role_id)
    await audit_service.log_action(
        action="role.deleted",
        user_id=current_user["id"],
        entity_type="role",
        entity_id=role_id,
        previous_value=role,
        new_value=None,
        request=request,
    )
    return None
