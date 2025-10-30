from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import api_keys as api_key_repo
from app.schemas.api_keys import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyDetailResponse,
    ApiKeyResponse,
    ApiKeyRotateRequest,
    ApiKeyUpdateRequest,
)
from app.security.api_keys import mask_api_key
from app.services import audit as audit_service

router = APIRouter(prefix="/api-keys", tags=["API Keys"])


def _format_response(row: dict) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row["id"],
        description=row.get("description"),
        expiry_date=row.get("expiry_date"),
        created_at=row.get("created_at"),
        last_used_at=row.get("last_used_at"),
        last_seen_at=row.get("last_seen_at"),
        usage_count=row.get("usage_count", 0),
        key_preview=mask_api_key(row.get("key_prefix")),
        usage=row.get("usage", []),
        permissions=row.get("permissions", []),
        allowed_ips=row.get("ip_restrictions", []),
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    search: str | None = Query(default=None, max_length=255),
    include_expired: bool = Query(default=False),
    order_by: str = Query(default="created_at"),
    order_direction: str = Query(default="desc"),
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
) -> list[ApiKeyResponse]:
    rows = await api_key_repo.list_api_keys_with_usage(
        search=search,
        include_expired=include_expired,
        order_by=order_by,
        order_direction=order_direction,
    )
    await audit_service.log_action(
        action="api_keys.list",
        user_id=user.get("id"),
        entity_type="api_key",
        request=None,
        metadata={"search": search, "include_expired": include_expired, "order_by": order_by, "order_direction": order_direction},
    )
    return [_format_response(row) for row in rows]


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
) -> ApiKeyCreateResponse:
    raw_key, row = await api_key_repo.create_api_key(
        description=payload.description,
        expiry_date=payload.expiry_date,
        permissions=[permission.model_dump() for permission in payload.permissions],
        ip_restrictions=[entry.cidr for entry in payload.allowed_ips],
    )
    formatted = _format_response(row).model_dump()
    await audit_service.log_action(
        action="api_keys.create",
        user_id=user.get("id"),
        entity_type="api_key",
        entity_id=row["id"],
        new_value={
            "description": payload.description,
            "expiry_date": payload.expiry_date.isoformat() if payload.expiry_date else None,
            "permissions": formatted.get("permissions", []),
            "allowed_ips": [entry.cidr for entry in payload.allowed_ips],
        },
        request=request,
    )
    return ApiKeyCreateResponse(api_key=raw_key, **formatted)


@router.get("/{api_key_id}", response_model=ApiKeyDetailResponse)
async def get_api_key(
    api_key_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> ApiKeyDetailResponse:
    row = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    return _format_response(row)


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    api_key_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
) -> None:
    row = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    await api_key_repo.delete_api_key(api_key_id)
    await audit_service.log_action(
        action="api_keys.delete",
        user_id=user.get("id"),
        entity_type="api_key",
        entity_id=api_key_id,
        previous_value={
            "description": row.get("description"),
            "expiry_date": row.get("expiry_date").isoformat() if row.get("expiry_date") else None,
            "key_preview": mask_api_key(row.get("key_prefix")),
        },
        request=request,
    )
    return None


@router.patch("/{api_key_id}", response_model=ApiKeyResponse)
async def update_api_key(
    api_key_id: int,
    payload: ApiKeyUpdateRequest,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
) -> ApiKeyResponse:
    existing = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    fields_set = payload.model_fields_set

    if "description" in fields_set:
        raw_description = (payload.description or "").strip()
        new_description = raw_description or None
    else:
        new_description = existing.get("description")

    if "expiry_date" in fields_set:
        new_expiry = payload.expiry_date
    else:
        new_expiry = existing.get("expiry_date")

    if "permissions" in fields_set:
        permissions_payload = [
            permission.model_dump() for permission in (payload.permissions or [])
        ]
        permissions_argument: list[dict[str, Any]] | None = permissions_payload
    else:
        permissions_payload = existing.get("permissions", [])
        permissions_argument = None

    updated = await api_key_repo.update_api_key(
        api_key_id,
        description=new_description,
        expiry_date=new_expiry,
        permissions=permissions_argument,
    )

    await audit_service.log_action(
        action="api_keys.update",
        user_id=user.get("id"),
        entity_type="api_key",
        entity_id=api_key_id,
        previous_value={
            "description": existing.get("description"),
            "expiry_date": existing.get("expiry_date").isoformat()
            if isinstance(existing.get("expiry_date"), date)
            else None,
            "permissions": existing.get("permissions", []),
        },
        new_value={
            "description": updated.get("description"),
            "expiry_date": updated.get("expiry_date").isoformat()
            if isinstance(updated.get("expiry_date"), date)
            else None,
            "permissions": updated.get("permissions", []),
        },
        request=request,
    )

    return _format_response(updated)


@router.post("/{api_key_id}/rotate", response_model=ApiKeyCreateResponse)
async def rotate_api_key(
    api_key_id: int,
    payload: ApiKeyRotateRequest,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(require_super_admin),
) -> ApiKeyCreateResponse:
    existing = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    new_description = payload.description if payload.description is not None else existing.get("description")
    new_expiry = payload.expiry_date if payload.expiry_date is not None else existing.get("expiry_date")
    permissions = (
        [permission.model_dump() for permission in payload.permissions]
        if payload.permissions is not None
        else existing.get("permissions", [])
    )
    allowed_ips = (
        [entry.cidr for entry in payload.allowed_ips]
        if payload.allowed_ips is not None
        else [entry.get("cidr") for entry in existing.get("ip_restrictions", [])]
    )
    raw_key, new_row = await api_key_repo.create_api_key(
        description=new_description,
        expiry_date=new_expiry,
        permissions=permissions,
        ip_restrictions=allowed_ips,
    )
    formatted = _format_response(new_row).model_dump()
    metadata = {
        "rotated_from": api_key_id,
        "retired_previous": bool(payload.retire_previous),
    }
    await audit_service.log_action(
        action="api_keys.rotate",
        user_id=user.get("id"),
        entity_type="api_key",
        entity_id=new_row["id"],
        previous_value=None,
        new_value={
            "description": new_description,
            "expiry_date": new_expiry.isoformat() if isinstance(new_expiry, date) else None,
            "permissions": formatted.get("permissions", []),
            "allowed_ips": [entry.cidr for entry in payload.allowed_ips]
            if payload.allowed_ips is not None
            else [entry.get("cidr") for entry in existing.get("ip_restrictions", [])],
        },
        metadata=metadata,
        request=request,
    )
    if payload.retire_previous:
        retirement_date = date.today()
        await api_key_repo.update_api_key_expiry(api_key_id, retirement_date)
        await audit_service.log_action(
            action="api_keys.retire",
            user_id=user.get("id"),
            entity_type="api_key",
            entity_id=api_key_id,
            previous_value={
                "description": existing.get("description"),
                "expiry_date": existing.get("expiry_date").isoformat()
                if isinstance(existing.get("expiry_date"), date)
                else None,
                "key_preview": mask_api_key(existing.get("key_prefix")),
            },
            new_value={"expiry_date": retirement_date.isoformat()},
            metadata={"rotated_to": new_row["id"]},
            request=request,
        )
    return ApiKeyCreateResponse(api_key=raw_key, **formatted)
