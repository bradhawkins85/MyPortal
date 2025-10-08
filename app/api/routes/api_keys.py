from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import api_keys as api_key_repo
from app.schemas.api_keys import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyDetailResponse,
    ApiKeyResponse,
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
