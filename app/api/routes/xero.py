from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeSerializer
from itsdangerous import BadSignature
from loguru import logger

from app.core.config import Settings, get_settings
from app.security.flash import flash_redirect
from app.core.logging import log_error, log_info
from app.repositories import invoices as invoice_repo
from app.repositories import users as user_repo
from app.security.session import session_manager
from app.services import modules as modules_service
from app.services import audit as audit_service

router = APIRouter(prefix="/api/integration-modules/xero", tags=["Xero"])
oauth_router = APIRouter(prefix="/xero", tags=["Xero OAuth"])

_settings: Settings | None = None


def _get_settings() -> Settings:
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


def _verify_xero_webhook_signature(body: bytes, signature: str | None, webhook_key: str | None) -> bool:
    """Validate Xero's HMAC-SHA256 webhook signature against the raw body."""

    key = (webhook_key or "").strip()
    provided = (signature or "").strip()
    if not key or not provided:
        return False
    digest = hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, provided)


def _extract_xero_invoice_id(event: dict[str, Any]) -> str | None:
    invoice_id = str(event.get("resourceId") or event.get("resourceID") or "").strip()
    if invoice_id:
        return invoice_id
    resource_url = str(event.get("resourceUrl") or event.get("resourceURL") or "").strip().rstrip("/")
    if "/Invoices/" in resource_url:
        return resource_url.rsplit("/", 1)[-1] or None
    return None


async def _fetch_xero_invoice(invoice_id: str) -> dict[str, Any] | None:
    credentials = await modules_service.get_xero_credentials()
    tenant_id = str((credentials or {}).get("tenant_id") or "").strip()
    if not tenant_id:
        raise RuntimeError("Xero tenant ID is not configured")
    access_token = await modules_service.acquire_xero_access_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"https://api.xero.com/api.xro/2.0/Invoices/{invoice_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "xero-tenant-id": tenant_id,
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
    invoices = response.json().get("Invoices") or []
    return invoices[0] if invoices else None


async def _apply_xero_invoice_event(event: dict[str, Any], request: Request) -> dict[str, Any]:
    category = str(event.get("eventCategory") or "").upper()
    if category != "INVOICE":
        return {"status": "ignored", "reason": "unsupported category"}
    invoice_id = _extract_xero_invoice_id(event)
    if not invoice_id:
        return {"status": "ignored", "reason": "missing invoice id"}

    local_invoice = await invoice_repo.get_invoice_by_xero_invoice_id(invoice_id)
    if not local_invoice:
        # Xero can send webhook events for invoices that were not created or
        # tracked by MyPortal.  Treat those events as successfully ignored before
        # doing any downstream processing so a foreign invoice cannot turn the
        # whole webhook delivery into a processing failure.
        return {"status": "ignored", "reason": "local invoice not found"}

    xero_invoice = await _fetch_xero_invoice(invoice_id)
    if not xero_invoice:
        return {"status": "ignored", "reason": "invoice not found in Xero"}
    xero_status = str(xero_invoice.get("Status") or "").strip().upper()
    if xero_status != "PAID":
        return {"status": "ignored", "reason": f"Xero status {xero_status or 'unknown'}"}
    if str(local_invoice.get("status") or "").strip().lower() == "paid":
        return {"status": "unchanged", "invoice_id": local_invoice.get("id")}

    updated = await invoice_repo.patch_invoice(int(local_invoice["id"]), status="paid")
    await audit_service.record(
        action="invoice.xero_webhook_paid",
        request=request,
        user_id=None,
        entity_type="invoice",
        entity_id=int(updated["id"]),
        before=local_invoice,
        after=updated,
        metadata={"xero_invoice_id": invoice_id, "xero_event": event},
    )
    return {"status": "updated", "invoice_id": updated.get("id")}


async def _ensure_module_enabled() -> dict[str, Any]:
    module = await modules_service.get_module("xero", redact=False)
    if not module or not module.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xero module is disabled",
        )
    return module


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    name="xero_receive_webhook",
)
async def receive_webhook(request: Request) -> Response:
    from app.services import webhook_monitor

    module = await _ensure_module_enabled()
    source_url = str(request.url)
    request_headers = dict(request.headers)
    body = await request.body()
    webhook_key = (module.get("settings") or {}).get("webhook_key")
    if not _verify_xero_webhook_signature(body, request.headers.get("x-xero-signature"), webhook_key):
        await webhook_monitor.log_incoming_webhook(
            name="Xero Webhook - Invalid Signature",
            source_url=source_url,
            payload=body.decode("utf-8", errors="replace"),
            headers=request_headers,
            response_status=401,
            response_body="",
            error_message="Invalid Xero webhook signature",
        )
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as exc:
        await webhook_monitor.log_incoming_webhook(
            name="Xero Webhook - Invalid JSON",
            source_url=source_url,
            payload=body.decode("utf-8", errors="replace"),
            headers=request_headers,
            response_status=400,
            response_body="Invalid JSON payload",
            error_message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc

    results = []
    for event in payload.get("events") or []:
        try:
            results.append(await _apply_xero_invoice_event(event, request))
        except Exception as exc:
            logger.exception("Failed to process Xero invoice webhook event")
            results.append({"status": "failed", "error": "Internal processing error"})

    # Xero disables webhook deliveries when receivers do not acknowledge valid
    # webhook notifications with a 2xx response quickly and consistently.  Event
    # processing is best-effort here: signature/JSON validation failures still
    # reject the request, but downstream invoice-sync errors are logged in the
    # webhook monitor without turning the provider delivery into a failed HTTP
    # attempt.
    await webhook_monitor.log_incoming_webhook(
        name="Xero Webhook - Invoice Updates",
        source_url=source_url,
        payload=payload,
        headers=request_headers,
        response_status=200,
        response_body=json.dumps({"status": "accepted", "results": results}),
        error_message="; ".join(
            str(result.get("error") or "Xero event processing failed")
            for result in results
            if result.get("status") == "failed"
        )
        or None,
    )
    return Response(status_code=status.HTTP_200_OK)


@router.post(
    "/callback",
    status_code=status.HTTP_202_ACCEPTED,
    name="xero_receive_callback",
)
async def receive_callback(request: Request) -> dict[str, str]:
    """Legacy Xero callback endpoint retained for existing callback integrations."""
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
        return flash_redirect("/admin/modules", "missing client id", "error")

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
        # Log the Xero-provided error but do not reflect it into the redirect
        # URL to avoid URL-redirection vulnerabilities (CodeQL py/url-redirection).
        message = request.query_params.get("error_description", error)
        log_error("Xero OAuth error", error=error, description=str(message)[:200])
        return flash_redirect("/admin/modules", "xero authorization failed", "error")

    if not code or not state:
        return flash_redirect("/admin/modules", "invalid response", "error")

    # Verify state token
    try:
        state_data = _get_state_serializer().loads(state)
    except BadSignature:
        return flash_redirect("/admin/modules", "invalid state", "error")

    credentials = await modules_service.get_xero_credentials()
    if not credentials:
        return flash_redirect("/admin/modules", "missing credentials", "error")

    client_id = credentials.get("client_id", "").strip()
    client_secret = credentials.get("client_secret", "").strip()

    if not (client_id and client_secret):
        return flash_redirect("/admin/modules", "incomplete credentials", "error")

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
        return flash_redirect("/admin/modules", "authorization failed", "error")
    except Exception as exc:
        log_error("Xero authorization error", error=str(exc))
        return flash_redirect("/admin/modules", "connection failed", "error")

    payload = response.json()
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")

    if not (access_token and refresh_token):
        log_error(
            "Xero token response missing tokens", payload_keys=list(payload.keys())
        )
        return flash_redirect("/admin/modules", "invalid token response", "error")

    # Calculate token expiry
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

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

        if connections:
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
    return flash_redirect("/admin/modules", "xero authorized", "success")
