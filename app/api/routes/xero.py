from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.services import modules as modules_service

router = APIRouter(prefix="/api/integration-modules/xero", tags=["Xero"])


async def _ensure_module_enabled() -> dict[str, Any]:
    module = await modules_service.get_module("xero", redact=False)
    if not module or not module.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xero module is disabled",
        )
    return module


@router.post(
    "/callback",
    status_code=status.HTTP_202_ACCEPTED,
    name="xero_receive_callback",
)
async def receive_callback(request: Request) -> dict[str, str]:
    await _ensure_module_enabled()

    body = await request.body()
    payload: dict[str, Any]
    if not body:
        payload = {}
    else:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            ) from exc

    xero_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower().startswith("x-xero-")
    }
    remote_addr = request.client.host if request.client else None
    logger.info(
        "Received Xero webhook callback",
        remote_addr=remote_addr,
        xero_headers=xero_headers,
        payload_keys=sorted(payload.keys()),
    )
    return {"status": "accepted"}


@router.get("/callback", name="xero_callback_probe")
async def probe_callback(request: Request) -> dict[str, str]:
    """Expose a lightweight probe endpoint for connectivity checks."""

    await _ensure_module_enabled()
    if request.query_params:
        logger.info(
            "Received Xero callback probe",
            query_params=dict(request.query_params),
        )
    return {"status": "ok"}


@router.get("/tenants", name="xero_list_tenants")
async def list_tenants() -> dict[str, Any]:
    """List available Xero tenants (organizations) for the configured credentials.
    
    Returns:
        Dictionary with tenants list and current tenant_id
    """
    module = await _ensure_module_enabled()
    
    # Get credentials
    credentials = await modules_service.get_xero_credentials()
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xero credentials not configured",
        )
    
    client_id = credentials.get("client_id", "").strip()
    client_secret = credentials.get("client_secret", "").strip()
    refresh_token = credentials.get("refresh_token", "").strip()
    
    if not (client_id and client_secret and refresh_token):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xero OAuth credentials incomplete",
        )
    
    try:
        # Get access token
        access_token = await modules_service.acquire_xero_access_token()
        
        # Fetch tenant connections
        async with httpx.AsyncClient(timeout=30.0) as client:
            connections_response = await client.get(
                "https://api.xero.com/connections",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            connections_response.raise_for_status()
            connections = connections_response.json()
        
        # Format tenant information
        tenants = [
            {
                "tenant_id": conn.get("tenantId"),
                "tenant_name": conn.get("tenantName"),
                "tenant_type": conn.get("tenantType"),
                "created_date_utc": conn.get("createdDateUtc"),
            }
            for conn in connections
        ]
        
        # Get current tenant_id from settings
        settings = module.get("settings") or {}
        current_tenant_id = settings.get("tenant_id", "")
        
        logger.info(
            "Listed Xero tenants",
            tenant_count=len(tenants),
            current_tenant_id=current_tenant_id,
        )
        
        return {
            "tenants": tenants,
            "current_tenant_id": current_tenant_id,
        }
    
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Failed to fetch Xero tenants",
            status_code=exc.response.status_code if exc.response else None,
            error=exc.response.text if exc.response else str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch Xero tenants: {exc.response.text if exc.response else str(exc)}",
        ) from exc
    except Exception as exc:
        logger.error("Error listing Xero tenants", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing Xero tenants: {str(exc)}",
        ) from exc
