from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeSerializer
from itsdangerous import BadSignature
from loguru import logger

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import users as user_repo
from app.security.session import session_manager
from app.services import modules as modules_service

router = APIRouter(prefix="/api/integration-modules/xero", tags=["Xero"])
oauth_router = APIRouter(prefix="/xero", tags=["Xero OAuth"])

_settings = None


def _get_settings():  # type: ignore[return]
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def _build_xero_redirect_uri() -> str:
    """Build Xero OAuth redirect URI using PORTAL_URL setting.

    This ensures the redirect_uri matches what's configured in the Xero OAuth
    app, preventing 'Invalid redirect_uri' errors when the app is behind a
    proxy or using a different hostname than the incoming request.
    """
    s = _get_settings()
    if s.portal_url:
        base = str(s.portal_url).rstrip("/")
        return f"{base}/xero/callback"
    # Fallback to relative path if PORTAL_URL not set
    return "/xero/callback"


def _get_state_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(_get_settings().secret_key, salt="xero-oauth")


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
    from app.services import webhook_monitor
    
    await _ensure_module_enabled()

    source_url = str(request.url)
    request_headers = dict(request.headers)
    
    body = await request.body()
    payload: dict[str, Any]
    if not body:
        payload = {}
    else:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            # Log the failed request
            await webhook_monitor.log_incoming_webhook(
                name="Xero Webhook - Invalid JSON",
                source_url=source_url,
                payload=body.decode("utf-8", errors="replace"),
                headers=request_headers,
                response_status=400,
                response_body="Invalid JSON payload",
                error_message=str(exc),
            )
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
    
    # Log the incoming webhook
    await webhook_monitor.log_incoming_webhook(
        name="Xero Webhook - Callback",
        source_url=source_url,
        payload=payload,
        headers=request_headers,
        response_status=202,
        response_body="Accepted",
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


# ---------------------------------------------------------------------------
# Xero OAuth2 routes
# ---------------------------------------------------------------------------


@oauth_router.get("/connect", name="xero_connect")
async def xero_connect(request: Request):
    """Initiate Xero OAuth2 authorization flow."""
    session_data = await session_manager.load_session(request)
    if not session_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # Check if user is super admin
    user = await user_repo.get_user_by_id(session_data.user_id)
    if not user or not user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super administrators can configure Xero integration",
        )

    credentials = await modules_service.get_xero_credentials()
    if not credentials:
        return RedirectResponse(
            url="/admin/modules", status_code=status.HTTP_303_SEE_OTHER
        )

    client_id = credentials.get("client_id", "").strip()
    if not client_id:
        return RedirectResponse(
            url="/admin/modules?error=missing+client+id",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Generate state token to prevent CSRF
    state = _get_state_serializer().dumps(
        {
            "user_id": session_data.user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    redirect_uri = _build_xero_redirect_uri()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "offline_access accounting.transactions accounting.contacts",
        "state": state,
    }
    authorize_url = (
        f"https://login.xero.com/identity/connect/authorize?{urlencode(params)}"
    )

    log_info(
        "Initiating Xero OAuth flow",
        user_id=session_data.user_id,
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@oauth_router.get("/callback", name="xero_callback")
async def xero_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Xero OAuth2 callback and exchange code for tokens."""
    if error:
        # Sanitize error message to prevent URL redirection attacks
        message = request.query_params.get("error_description", error)
        safe_message = "".join(
            c if c.isalnum() or c in " .-_" else "" for c in str(message)[:200]
        )
        encoded = urlencode({"error": safe_message or "authorization_error"})
        return RedirectResponse(
            url=f"/admin/modules?{encoded}", status_code=status.HTTP_303_SEE_OTHER
        )

    if not code or not state:
        return RedirectResponse(
            url="/admin/modules?error=invalid+response",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Verify state token
    try:
        state_data = _get_state_serializer().loads(state)
    except BadSignature:
        return RedirectResponse(
            url="/admin/modules?error=invalid+state",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    credentials = await modules_service.get_xero_credentials()
    if not credentials:
        return RedirectResponse(
            url="/admin/modules?error=missing+credentials",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    client_id = credentials.get("client_id", "").strip()
    client_secret = credentials.get("client_secret", "").strip()

    if not (client_id and client_secret):
        return RedirectResponse(
            url="/admin/modules?error=incomplete+credentials",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Exchange authorization code for tokens
    token_url = "https://identity.xero.com/connect/token"
    redirect_uri = _build_xero_redirect_uri()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data=data,
                auth=(client_id, client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_error(
            "Xero authorization failed",
            status=exc.response.status_code if exc.response else None,
            body=exc.response.text if exc.response else None,
        )
        return RedirectResponse(
            url="/admin/modules?error=authorization+failed",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as exc:
        log_error("Xero authorization error", error=str(exc))
        return RedirectResponse(
            url="/admin/modules?error=connection+failed",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    payload = response.json()
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")

    if not (access_token and refresh_token):
        log_error(
            "Xero token response missing tokens", payload_keys=list(payload.keys())
        )
        return RedirectResponse(
            url="/admin/modules?error=invalid+token+response",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Calculate token expiry
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))

    # Fetch tenant connections to get tenant_id
    tenant_id = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            connections_response = await client.get(
                "https://api.xero.com/connections",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
        connections_response.raise_for_status()
        connections = connections_response.json()

        if connections and len(connections) > 0:
            tenant_id = connections[0].get("tenantId")
            if tenant_id:
                log_info("Discovered Xero tenant_id", tenant_id=tenant_id)
    except Exception as exc:
        # Don't fail the entire flow if we can't fetch connections
        log_error("Failed to fetch Xero connections", error=str(exc))

    # Store tokens and tenant_id (tokens will be encrypted by update_xero_tokens)
    await modules_service.update_xero_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        tenant_id=tenant_id,
    )

    log_info(
        "Xero OAuth callback processed successfully",
        user_id=state_data.get("user_id"),
    )
    return RedirectResponse(
        url="/admin/modules?success=xero+authorized",
        status_code=status.HTTP_303_SEE_OTHER,
    )
