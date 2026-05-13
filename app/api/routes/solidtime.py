"""HTTP endpoints for the Solidtime integration module."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import require_super_admin
from app.repositories import tickets as tickets_repo
from app.schemas.solidtime import (
    SolidtimeOrganisation,
    SolidtimeOrganisationListResponse,
    SolidtimeSyncResponse,
    SolidtimeTestConnectionResponse,
)
from app.services import solidtime as solidtime_service
from app.services import webhook_monitor

router = APIRouter(
    prefix="/api/v1/solidtime",
    tags=["Solidtime"],
)


@router.get(
    "/organizations",
    response_model=SolidtimeOrganisationListResponse,
    summary="List Solidtime organisations",
    description=(
        "Returns the organisations the configured Solidtime API token has "
        "access to. Used by the admin module page to populate the "
        "organisation drop-down."
    ),
)
async def list_organizations(
    _user: dict = Depends(require_super_admin),
) -> SolidtimeOrganisationListResponse:
    try:
        memberships = await solidtime_service.list_organizations()
    except solidtime_service.SolidtimeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except solidtime_service.SolidtimeAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        )
    organisations = [
        SolidtimeOrganisation(
            id=str(item.get("organization_id") or item.get("id") or ""),
            name=str(item.get("organization_name") or item.get("name") or "")
            or None,
            role=str(item.get("role") or "") or None,
        )
        for item in memberships
        if isinstance(item, dict)
        and (item.get("organization_id") or item.get("id"))
    ]
    return SolidtimeOrganisationListResponse(organizations=organisations)


@router.post(
    "/test-connection",
    response_model=SolidtimeTestConnectionResponse,
    summary="Test the Solidtime API token",
    description=(
        "Performs a single authenticated call against Solidtime to verify "
        "the configured token. Returns the list of organisations on success."
    ),
)
async def test_connection(
    _user: dict = Depends(require_super_admin),
) -> SolidtimeTestConnectionResponse:
    try:
        memberships = await solidtime_service.list_organizations()
    except solidtime_service.SolidtimeConfigurationError as exc:
        return SolidtimeTestConnectionResponse(ok=False, message=str(exc))
    except solidtime_service.SolidtimeAPIError as exc:
        return SolidtimeTestConnectionResponse(ok=False, message=str(exc))
    organisations = [
        SolidtimeOrganisation(
            id=str(item.get("organization_id") or item.get("id") or ""),
            name=str(item.get("organization_name") or item.get("name") or "")
            or None,
            role=str(item.get("role") or "") or None,
        )
        for item in memberships
        if isinstance(item, dict)
    ]
    return SolidtimeTestConnectionResponse(
        ok=True,
        message=f"Connected. {len(organisations)} organisation(s) accessible.",
        organizations=organisations,
    )


@router.post(
    "/sync/ticket/{ticket_id}",
    response_model=SolidtimeSyncResponse,
    summary="Manually push a ticket to Solidtime",
    description=(
        "Creates or updates the Solidtime project mirroring the given ticket. "
        "Returns the resulting project URL and link metadata."
    ),
)
async def sync_ticket(
    ticket_id: int,
    _user: dict = Depends(require_super_admin),
) -> SolidtimeSyncResponse:
    ticket = await tickets_repo.get_ticket(int(ticket_id))
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )
    try:
        link = await solidtime_service.sync_ticket_to_project(int(ticket_id))
    except solidtime_service.SolidtimeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except solidtime_service.SolidtimeAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        )
    if not link:
        return SolidtimeSyncResponse(ok=False, detail="Solidtime sync is disabled")
    ticket_links = await solidtime_service.get_ticket_links(int(ticket_id))
    return SolidtimeSyncResponse(
        ok=True,
        detail="Ticket synchronised to Solidtime",
        project_id=str(link.get("solidtime_project_id") or "") or None,
        project_url=ticket_links.get("project_url") or None,
    )


@router.post(
    "/sync/reply/{reply_id}",
    response_model=SolidtimeSyncResponse,
    summary="Manually push a ticket reply's time entry to Solidtime",
)
async def sync_reply(
    reply_id: int,
    _user: dict = Depends(require_super_admin),
) -> SolidtimeSyncResponse:
    reply = await tickets_repo.get_reply_by_id(int(reply_id))
    if not reply:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reply not found"
        )
    try:
        link = await solidtime_service.sync_reply_to_time_entry(int(reply_id))
    except solidtime_service.SolidtimeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except solidtime_service.SolidtimeAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        )
    if not link:
        return SolidtimeSyncResponse(
            ok=False,
            detail="Reply does not have time tracked or Solidtime sync is disabled",
        )
    return SolidtimeSyncResponse(
        ok=True,
        detail="Time entry synchronised to Solidtime",
        time_entry_id=str(link.get("solidtime_time_entry_id") or "") or None,
    )


@router.post(
    "/reconcile",
    summary="Run the inbound Solidtime reconciliation now",
    description=(
        "Triggers the same reconciliation loop as the scheduled "
        "``solidtime-reconcile`` job. Useful when an admin has just connected "
        "Solidtime and wants an immediate pull."
    ),
)
async def reconcile_now(
    _user: dict = Depends(require_super_admin),
) -> dict[str, Any]:
    result = await solidtime_service.reconcile_once()
    if result.get("status") == "skipped" and result.get("reason"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(result["reason"]),
        )
    return result


@router.post(
    "/webhook",
    summary="Receive Solidtime webhook events",
    description=(
        "Inbound webhook receiver for Solidtime forwarders. The configured "
        "``webhook_secret`` is used to HMAC-verify the payload via the "
        "``X-Solidtime-Signature`` header."
    ),
    include_in_schema=True,
)
async def receive_webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    headers = dict(request.headers)
    settings = await solidtime_service._load_module_settings() or {}
    secret = str(settings.get("webhook_secret") or "")
    signature = headers.get("x-solidtime-signature") or headers.get(
        "X-Solidtime-Signature"
    )
    if not secret or not solidtime_service.verify_webhook_signature(
        secret, raw_body, signature
    ):
        await webhook_monitor.log_incoming_webhook(
            name="Solidtime Webhook - Invalid signature",
            source_url=str(request.url),
            headers=headers,
            response_status=401,
            response_body="Invalid signature",
            error_message="Missing or invalid webhook signature",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    try:
        payload = await request.json()
    except (ValueError, json.JSONDecodeError):
        payload = None

    await webhook_monitor.log_incoming_webhook(
        name="Solidtime Webhook",
        source_url=str(request.url),
        headers=headers,
        response_status=200,
        response_body="ok",
    )
    # The actual event routing is delegated to the reconciler so that a
    # webhook miss does not cause data divergence.
    return JSONResponse(
        content={"ok": True, "event": (payload or {}).get("type")},
        status_code=200,
    )
