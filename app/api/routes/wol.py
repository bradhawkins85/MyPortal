"""HTTP routes for Wake-on-LAN (WoL).

Two groups, matching the tray route conventions:

* Technician endpoints (``/api/wol/...``) -- a signed-in technician (or super
  admin) triggers a manual "Wake" for an asset or device, or reads attempt
  history and per-asset success stats.
* Device-facing endpoint (``/api/tray/wol/...``) -- an on-network agent
  reports that it broadcast the WoL magic packet, authenticated by the
  per-device bearer token.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import (
    get_current_tray_device,
    require_helpdesk_technician,
)
from app.repositories import user_companies as user_company_repo
from app.repositories import wol as wol_repo
from app.services import audit as audit_service
from app.services import wol as wol_service

router = APIRouter(tags=["Wake-on-LAN"])


async def _assert_company_access(user: dict, company_id: int | None) -> None:
    """Ensure the acting user may act on ``company_id`` (super admin bypasses)."""

    if company_id is None:
        return
    if user.get("is_super_admin"):
        return
    try:
        membership = await user_company_repo.get_user_company(int(user["id"]), int(company_id))
    except (KeyError, TypeError, ValueError):
        membership = None
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Company access required")


async def _target_company_id(asset_id: int | None, device_id: int | None) -> int | None:
    from app.repositories import assets as assets_repo
    from app.repositories import tray as tray_repo

    if asset_id is not None:
        asset = await assets_repo.get_asset_by_id(int(asset_id))
        return int(asset["company_id"]) if asset and asset.get("company_id") is not None else None
    if device_id is not None:
        device = await tray_repo.get_device_by_id(int(device_id))
        return int(device["company_id"]) if device and device.get("company_id") is not None else None
    return None


@router.post("/api/wol/assets/{asset_id}/wake")
async def wake_asset_endpoint(
    asset_id: int,
    request: Request,
    current_user: dict = Depends(require_helpdesk_technician),
) -> dict[str, Any]:
    """Manually trigger a Wake-on-LAN attempt for an asset."""

    company_id = await _target_company_id(asset_id, None)
    await _assert_company_access(current_user, company_id)
    result = await wol_service.wake_asset(asset_id, current_user.get("id"))
    await audit_service.log_action(
        action="wol.attempt.started",
        user_id=current_user.get("id"),
        entity_type="asset",
        entity_id=asset_id,
        metadata=result,
        request=request,
    )
    return result


@router.post("/api/wol/devices/{device_id}/wake")
async def wake_device_endpoint(
    device_id: int,
    request: Request,
    current_user: dict = Depends(require_helpdesk_technician),
) -> dict[str, Any]:
    """Manually trigger a Wake-on-LAN attempt for a tray device's asset."""

    company_id = await _target_company_id(None, device_id)
    await _assert_company_access(current_user, company_id)
    result = await wol_service.wake_device(device_id, current_user.get("id"))
    await audit_service.log_action(
        action="wol.attempt.started",
        user_id=current_user.get("id"),
        entity_type="tray_device",
        entity_id=device_id,
        metadata=result,
        request=request,
    )
    return result


@router.get("/api/wol/assets/{asset_id}/attempts")
async def list_attempts_endpoint(
    asset_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(require_helpdesk_technician),
) -> dict[str, Any]:
    """Return recent WoL attempts and success stats for an asset."""

    from app.repositories import assets as assets_repo

    asset = await assets_repo.get_asset_by_id(int(asset_id))
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    await _assert_company_access(current_user, int(asset.get("company_id") or 0) or None)
    attempts = await wol_repo.list_attempts_by_asset(asset_id, limit)
    for attempt in attempts:
        if isinstance(attempt.get("sender_device_ids"), str):
            try:
                attempt["sender_device_ids"] = json.loads(attempt["sender_device_ids"])
            except (ValueError, TypeError):
                attempt["sender_device_ids"] = []
    return {"asset_id": asset_id, "attempts": attempts, "stats": await wol_repo.asset_stats(asset_id)}


@router.post("/api/tray/wol/attempts/{attempt_id}/sent")
async def tray_wol_sent(
    attempt_id: int,
    request: Request,
    device: dict = Depends(get_current_tray_device),
) -> dict[str, Any]:
    """An on-network agent reports it broadcast the WoL packet."""

    payload = {}
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - body is optional
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    await wol_service.record_agent_sent(int(attempt_id), int(device["id"]), error)
    return {"status": "accepted"}
