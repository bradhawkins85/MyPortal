"""Documented API for Microsoft 365 standards policies (super administrators only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import m365_policies as policy_repo
from app.schemas.m365_policies import (
    PolicyPreviewRequest,
    PolicyPreviewResponse,
    PolicyRead,
    PolicyWrite,
    StandardResult,
    StandardSummary,
)
from app.services import m365_policies as policy_service
from app.services import m365_policy_targeting as targeting
from app.services import m365_standards as standards

router = APIRouter(
    prefix="/api/m365/policies",
    tags=["Microsoft 365 Policies"],
    dependencies=[Depends(require_database)],
)


async def _policy_or_404(policy_id: int) -> dict:
    policy = await policy_repo.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.get("/standards", response_model=list[StandardSummary], summary="List standards a policy can apply")
async def list_standards(user: dict = Depends(require_super_admin)):
    return standards.catalogue()


@router.get("/fields", summary="List tenant filter fields and their operators")
async def list_fields(user: dict = Depends(require_super_admin)):
    return {"fields": targeting.field_catalogue()}


@router.post("/preview", response_model=PolicyPreviewResponse, summary="Preview the tenants a draft filter targets")
async def preview(payload: PolicyPreviewRequest, user: dict = Depends(require_super_admin)):
    try:
        return await policy_service.preview_targets(
            payload.target_rules, [item.model_dump() for item in payload.overrides]
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/results", response_model=list[StandardResult], summary="List the latest standard results")
async def list_results(
    company_id: int | None = Query(default=None, gt=0), user: dict = Depends(require_super_admin)
):
    return await policy_repo.list_results(company_id)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED, summary="Start a standards run for every tenant")
async def run_now(user: dict = Depends(require_super_admin)):
    policy_service.start_background_run()
    return {"status": "started"}


@router.get("", response_model=list[PolicyRead], summary="List policies")
async def list_policies(user: dict = Depends(require_super_admin)):
    return await policy_repo.list_policies()


@router.post("", response_model=PolicyRead, status_code=status.HTTP_201_CREATED, summary="Create a policy")
async def create_policy(payload: PolicyWrite, request: Request, user: dict = Depends(require_super_admin)):
    try:
        policy_id = await policy_service.save_policy_audited(
            payload.model_dump(), user_id=user.get("id"), request=request
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _policy_or_404(policy_id)


@router.get("/{policy_id}", response_model=PolicyRead, summary="Get a policy")
async def get_policy(policy_id: int, user: dict = Depends(require_super_admin)):
    return await _policy_or_404(policy_id)


@router.put("/{policy_id}", response_model=PolicyRead, summary="Replace a policy")
async def update_policy(
    policy_id: int, payload: PolicyWrite, request: Request, user: dict = Depends(require_super_admin)
):
    try:
        await policy_service.save_policy_audited(
            payload.model_dump(), policy_id=policy_id, user_id=user.get("id"), request=request
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _policy_or_404(policy_id)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a policy")
async def delete_policy(policy_id: int, request: Request, user: dict = Depends(require_super_admin)):
    try:
        await policy_service.delete_policy(policy_id, user_id=user.get("id"), request=request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
