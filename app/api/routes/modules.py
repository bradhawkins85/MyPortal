from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies.auth import require_super_admin
from app.repositories import integration_modules as module_repo
from app.schemas.integration_modules import IntegrationModuleResponse, IntegrationModuleUpdate
from app.services import modules as modules_service
from app.services import audit as audit_service
from app.services.component_availability import AvailabilityConfigurationError, get_component_availability

router = APIRouter(prefix="/api/integration-modules", tags=["Integration Modules"])


def _audit_snapshot(module: dict) -> dict:
    """Flatten settings so the central diff reports individual changed names."""

    snapshot = {"enabled": bool(module.get("enabled"))}
    settings = module.get("settings") or {}
    if isinstance(settings, dict):
        snapshot.update({f"setting.{key}": value for key, value in settings.items()})
    return snapshot


@router.get("/", response_model=list[IntegrationModuleResponse])
async def list_modules(current_user: dict = Depends(require_super_admin)) -> list[IntegrationModuleResponse]:
    modules = await modules_service.list_modules()
    return [IntegrationModuleResponse(**module) for module in modules]


@router.get("/{slug}", response_model=IntegrationModuleResponse)
async def get_module(slug: str, current_user: dict = Depends(require_super_admin)) -> IntegrationModuleResponse:
    module = await modules_service.get_module(slug)
    if not module:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    return IntegrationModuleResponse(**module)


@router.put("/{slug}", response_model=IntegrationModuleResponse)
async def update_module(
    slug: str,
    payload: IntegrationModuleUpdate,
    current_user: dict = Depends(require_super_admin),
    request: Request = None,
) -> IntegrationModuleResponse:
    if not get_component_availability().module_available(slug):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    exists = await module_repo.get_module(slug)
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    try:
        updated = await modules_service.update_module(
            slug, enabled=payload.enabled, settings=payload.settings
        )
    except AvailabilityConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not updated:
        updated = await modules_service.get_module(slug)
    await audit_service.record(
        action="integration.module.configure",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="integration_module",
        before=_audit_snapshot(exists),
        after=_audit_snapshot(updated),
        metadata={"module": slug},
    )
    return IntegrationModuleResponse(**updated)
