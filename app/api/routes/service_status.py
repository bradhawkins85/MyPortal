from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.api_keys import get_optional_api_key
from app.api.dependencies.auth import get_current_user, get_optional_user, require_super_admin
from app.repositories import user_companies as user_company_repo
from app.schemas.service_status import (
    ServiceStatusCreate,
    ServiceStatusResponse,
    ServiceStatusUpdate,
    ServiceStatusUpdateStatusRequest,
)
from app.security.session import session_manager
from app.services import company_access
from app.services import service_status as service_status_service

router = APIRouter(prefix="/api/service-status", tags=["Service Status"])


def _serialize_service(service: dict[str, Any]) -> ServiceStatusResponse:
    tags = service.get("tags") or []
    if isinstance(tags, str):
        # Parse from comma-separated string if needed
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    return ServiceStatusResponse(
        id=service["id"],
        name=service["name"],
        description=service.get("description"),
        status=service.get("status") or service_status_service.DEFAULT_STATUS,
        status_message=service.get("status_message"),
        display_order=int(service.get("display_order") or 0),
        is_active=bool(service.get("is_active", True)),
        company_ids=list(service.get("company_ids") or []),
        tags=list(tags),
        created_at=service.get("created_at"),
        updated_at=service.get("updated_at"),
        updated_by=service.get("updated_by"),
    )


async def _ensure_company_access(user: dict, company_id: int) -> None:
    if user.get("is_super_admin"):
        return
    user_id = user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        user_id_int = None
    if user_id_int is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    membership = await user_company_repo.get_user_company(user_id_int, company_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def _resolve_company_id(
    request: Request,
    user: dict,
    requested_company_id: int | None,
) -> int | None:
    if requested_company_id is not None:
        company_id = int(requested_company_id)
        await _ensure_company_access(user, company_id)
        return company_id
    session = await session_manager.load_session(request)
    if session and session.active_company_id is not None:
        company_id = int(session.active_company_id)
        if user.get("is_super_admin"):
            return company_id
        try:
            user_id_int = int(user.get("id"))
        except (TypeError, ValueError):
            user_id_int = None
        if user_id_int is not None:
            membership = await user_company_repo.get_user_company(user_id_int, company_id)
            if membership:
                return company_id
    fallback = await company_access.first_accessible_company_id(user)
    if fallback is None:
        return None
    return int(fallback)


async def _get_status_update_actor(
    optional_user: dict | None = Depends(get_optional_user),
    api_key_record: dict | None = Depends(get_optional_api_key),
) -> dict[str, Any]:
    if api_key_record:
        return {"api_key": api_key_record, "user": None}
    if optional_user:
        if not optional_user.get("is_super_admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admin privileges required",
            )
        return {"api_key": None, "user": optional_user}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


@router.get("/services", response_model=list[ServiceStatusResponse])
async def list_services(
    request: Request,
    company_id: int | None = Query(
        None,
        description="Limit results to a specific company. Defaults to the active company context.",
    ),
    include_inactive: bool = Query(False, description="Include inactive services (super admin only)."),
    current_user: dict = Depends(get_current_user),
) -> list[ServiceStatusResponse]:
    resolved_company_id = await _resolve_company_id(request, current_user, company_id)
    include_all = bool(include_inactive and current_user.get("is_super_admin"))
    services = await service_status_service.list_services_for_company(
        resolved_company_id,
        include_inactive=include_all,
    )
    return [_serialize_service(service) for service in services]


@router.post("/services", response_model=ServiceStatusResponse, status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: ServiceStatusCreate,
    current_user: dict = Depends(require_super_admin),
) -> ServiceStatusResponse:
    try:
        created = await service_status_service.create_service(
            payload.dict(),
            company_ids=payload.company_ids,
            updated_by=int(current_user.get("id")) if current_user.get("id") else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create service")
    return _serialize_service(created)


@router.put("/services/{service_id}", response_model=ServiceStatusResponse)
async def update_service(
    service_id: int,
    payload: ServiceStatusUpdate,
    current_user: dict = Depends(require_super_admin),
) -> ServiceStatusResponse:
    try:
        updated = await service_status_service.update_service(
            service_id,
            payload.dict(exclude_unset=True),
            company_ids=payload.company_ids,
            updated_by=int(current_user.get("id")) if current_user.get("id") else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return _serialize_service(updated)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    service_id: int,
    _: dict = Depends(require_super_admin),
) -> None:
    await service_status_service.delete_service(service_id)


@router.patch("/services/{service_id}/status", response_model=ServiceStatusResponse)
async def update_service_status(
    service_id: int,
    payload: ServiceStatusUpdateStatusRequest,
    actor: dict[str, Any] = Depends(_get_status_update_actor),
) -> ServiceStatusResponse:
    updated_by = None
    user = actor.get("user") if isinstance(actor, dict) else None
    if user and user.get("id"):
        try:
            updated_by = int(user.get("id"))
        except (TypeError, ValueError):
            updated_by = None
    try:
        updated = await service_status_service.update_service_status(
            service_id,
            status=payload.status,
            status_message=payload.status_message,
            updated_by=updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return _serialize_service(updated)


@router.post("/services/{service_id}/refresh-tags", response_model=ServiceStatusResponse)
async def refresh_service_tags(
    service_id: int,
    current_user: dict = Depends(require_super_admin),
) -> ServiceStatusResponse:
    """Regenerate AI tags for a service."""
    try:
        updated = await service_status_service.refresh_service_tags(service_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return _serialize_service(updated)
