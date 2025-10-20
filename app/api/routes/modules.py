from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.repositories import integration_modules as module_repo
from app.schemas.integration_modules import IntegrationModuleResponse, IntegrationModuleUpdate
from app.services import modules as modules_service

router = APIRouter(prefix="/api/integration-modules", tags=["Integration Modules"])


@router.get("/", response_model=list[IntegrationModuleResponse])
async def list_modules(current_user: dict = Depends(require_super_admin)) -> list[IntegrationModuleResponse]:
    modules = await modules_service.list_modules()
    return [IntegrationModuleResponse(**module) for module in modules]


@router.get("/{slug}", response_model=IntegrationModuleResponse)
async def get_module(slug: str, current_user: dict = Depends(require_super_admin)) -> IntegrationModuleResponse:
    module = await module_repo.get_module(slug)
    if not module:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    return IntegrationModuleResponse(**module)


@router.put("/{slug}", response_model=IntegrationModuleResponse)
async def update_module(
    slug: str,
    payload: IntegrationModuleUpdate,
    current_user: dict = Depends(require_super_admin),
) -> IntegrationModuleResponse:
    module = await module_repo.get_module(slug)
    if not module:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    updated = await modules_service.update_module(slug, enabled=payload.enabled, settings=payload.settings)
    if not updated:
        updated = await module_repo.get_module(slug)
    return IntegrationModuleResponse(**updated)


@router.post("/{slug}/test", status_code=status.HTTP_200_OK)
async def test_module(slug: str, current_user: dict = Depends(require_super_admin)) -> dict[str, str | int | None]:
    result = await modules_service.test_module(slug)
    if result.get("status") == "error":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result)
    return result

