from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info, log_warning
from app.repositories import apps as apps_repo
from app.repositories import companies as companies_repo
from app.repositories import licenses as license_repo
from app.repositories import integration_modules as modules_repo
from app.repositories import m365 as m365_repo
from app.repositories import staff as staff_repo
from app.security.encryption import decrypt_secret, encrypt_secret


_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Exchange Online (Office 365 Exchange Online) service principal app ID and scope.
# Used to acquire app-only tokens for Exchange Online PowerShell REST API calls
# (e.g. Get-MailboxPermission) which are not available via Microsoft Graph.
_EXO_APP_ID = "00000002-0000-0ff1-ce00-000000000000"
_EXO_SCOPE = "https://outlook.office365.com/.default"
# Exchange.ManageAsApp application role – grants app-only access to Exchange Online
# PowerShell cmdlets when combined with an appropriate Exchange RBAC role assignment.
_EXO_MANAGE_AS_APP_ROLE = "dc50a0fb-09a3-484d-be87-e023b12c6440"

# Pattern matching auto-generated package mailbox names, e.g. package_9024cbae-6e9a-4cee-934e-5f05143cd7ae
_PACKAGE_MAILBOX_RE = re.compile(
    r"^package_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Microsoft Graph's own well-known app ID (constant across all tenants)
_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# Application-permission role IDs required for the provisioned integration app.
# The CIS benchmark checks require several additional read-only permissions so
# that the app can inspect security policies, Intune compliance policies, and
# audit logs.  These are included in the initial provisioning grant so that
# newly provisioned apps immediately support CIS benchmarking without requiring
# re-provisioning.
_PROVISION_APP_ROLES: list[str] = [
    "df021288-bdef-4463-88db-98f22de89214",  # User.Read.All
    "7ab1d382-f21e-4acd-a863-ba3e13f7da61",  # Directory.Read.All
    "18a4783c-866b-4cc7-a460-3d5e5662c884",  # Application.ReadWrite.OwnedBy (for self-renewal)
    # Additional permissions for CIS benchmark checks:
    "246dd0d5-5bd0-4def-940b-0421030a5b68",  # Policy.Read.All
    "498476ce-e0fe-48b0-b801-37ba7e2685c6",  # Organization.Read.All
    "dc377aa6-52d8-4e23-b271-2a7ae04cedf3",  # DeviceManagementConfiguration.Read.All
    "2f51be20-0bb4-4fed-bf7b-db946066c75e",  # DeviceManagementManagedDevices.Read.All
    "b0afded3-3588-46d8-8b3d-9842eff778da",  # AuditLog.Read.All
    # Additional permissions for mailbox reporting:
    "230c1aed-a721-4c5d-9cb4-a90514e508ef",  # Reports.Read.All
    "40f97065-369a-49f4-947c-6a255697ae91",  # MailboxSettings.Read
]

# OAuth scopes requested during the admin-consent provisioning flow
PROVISION_SCOPE = (
    "https://graph.microsoft.com/Application.ReadWrite.All "
    "https://graph.microsoft.com/AppRoleAssignment.ReadWrite.All offline_access"
)

# Delegated scopes requested during the "Authorize portal access" (connect) flow.
# The connect callback calls try_grant_missing_permissions() which needs
# AppRoleAssignment.ReadWrite.All to add any newly-required application permissions
# (e.g. MailboxSettings.Read) and Directory.Read.All to look up service principals.
# Using explicit scopes instead of ``/.default`` ensures the admin grants these
# delegated permissions even if they are not statically configured on the enterprise
# app registration (Microsoft Entra ID dynamic consent).
CONNECT_SCOPE = (
    "https://graph.microsoft.com/AppRoleAssignment.ReadWrite.All "
    "https://graph.microsoft.com/Directory.Read.All offline_access"
)

# Minimal scopes used for the tenant-discovery sign-in step
DISCOVER_SCOPE = "openid profile"

# Scopes for CSP/Lighthouse GDAP sign-in (needs Directory.Read.All for /contracts)
CSP_SCOPE = (
    "https://graph.microsoft.com/Directory.Read.All openid profile offline_access"
)

# Module slug used to store the admin / CSP / Lighthouse partner app credentials
_M365_ADMIN_MODULE_SLUG = "m365-admin"

# Application-permission role IDs for the provisioned CSP/Lighthouse admin app.
# Directory.Read.All (application) is required to enumerate /contracts via GDAP.
# Application.ReadWrite.OwnedBy allows the app to renew its own client secret.
_CSP_ADMIN_APP_ROLES: list[str] = [
    "7ab1d382-f21e-4acd-a863-ba3e13f7da61",  # Directory.Read.All (application)
    "18a4783c-866b-4cc7-a460-3d5e5662c884",  # Application.ReadWrite.OwnedBy (for self-renewal)
]

# Delegated scope ID for Directory.Read.All (for CSP sign-in by partner admins)
_DIRECTORY_READ_ALL_SCOPE_ID = "06da0dbc-49e2-44d2-8312-53f166ab848a"

# Well-known Microsoft public client used as a fallback for PKCE-based bootstrap
# provisioning when no custom PKCE client is configured.  This is the Azure CLI
# application registered by Microsoft.  Note: some Azure AD tenants restrict
# external applications via Conditional Access or app-approval policies, which
# can prevent this client from being used.  Set M365_PKCE_CLIENT_ID to a public
# client app registration in your own tenant to avoid this issue.
_AZURE_CLI_CLIENT_ID = "04b07795-8542-4ab8-9e00-81f6b0a2c83a"


def get_pkce_client_id() -> str:
    """Return the PKCE public-client app ID to use for the bootstrap provisioning flow.

    Prefers the operator-configured ``M365_PKCE_CLIENT_ID`` setting (a public
    client app registration created in the partner tenant).  Falls back to the
    well-known Azure CLI client ID when no custom value is configured.

    Some Azure AD tenants block external applications (e.g. via Conditional
    Access or tenant app-approval policies), which causes the Azure CLI fallback
    to fail with ``AADSTS700016``.  In those cases, create a new app registration
    in your Azure AD tenant, enable *Allow public client flows*, and set
    ``M365_PKCE_CLIENT_ID`` to its Application (client) ID.
    """
    configured = str(get_settings().m365_pkce_client_id or "").strip()
    return configured if configured else _AZURE_CLI_CLIENT_ID


class M365Error(RuntimeError):
    """Raised when Microsoft 365 operations fail.

    :param http_status: The HTTP status code from the Microsoft Graph API
        response that triggered this error, if applicable.  ``None`` for errors
        not associated with a specific HTTP response.
    """

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status: int | None = http_status


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE ``code_verifier`` / ``code_challenge`` pair.

    Returns a tuple of ``(code_verifier, code_challenge)`` where the challenge
    is the URL-safe base64-encoded SHA-256 hash of the verifier (S256 method).
    The verifier is a 32-byte cryptographically random string.
    """
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    )
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def extract_tenant_id_from_token(token: str) -> str:
    """Extract the Azure AD tenant ID (``tid`` claim) from a JWT token.

    The ``tid`` claim is present in both ``id_token`` and ``access_token``
    responses from Azure AD and uniquely identifies the tenant.

    The JWT signature is **not** verified here — we trust that the token was
    received directly from Microsoft's token endpoint over HTTPS.

    Raises :class:`M365Error` if the token is malformed or does not contain a
    ``tid`` claim.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            raise M365Error("Malformed JWT: expected at least two segments")
        # JWT uses base64url encoding without padding; restore padding before decoding
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except M365Error:
        raise
    except Exception as exc:
        raise M365Error(f"Failed to decode JWT payload: {exc}") from exc

    tid = str(payload.get("tid") or "").strip()
    if not tid:
        raise M365Error("Tenant ID (tid) not found in token claims")
    return tid


def _decrypt(field: str | None) -> str | None:
    if not field:
        return None
    return decrypt_secret(field)


def _encrypt(field: str | None) -> str | None:
    if not field:
        return None
    return encrypt_secret(field)


def parse_graph_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string from Microsoft Graph into a UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


async def get_credentials(company_id: int) -> dict[str, Any] | None:
    record = await m365_repo.get_credentials(company_id)
    if not record:
        return None
    decrypted = record.copy()
    for key in ("client_secret", "refresh_token", "access_token"):
        decrypted[key] = _decrypt(decrypted.get(key))
    return decrypted


async def upsert_credentials(
    *,
    company_id: int,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    app_object_id: str | None = None,
    client_secret_key_id: str | None = None,
    client_secret_expires_at: datetime | None = None,
) -> dict[str, Any]:
    await m365_repo.upsert_credentials(
        company_id=company_id,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=_encrypt(client_secret),
        refresh_token=None,
        access_token=None,
        token_expires_at=None,
        app_object_id=app_object_id,
        client_secret_key_id=client_secret_key_id,
        client_secret_expires_at=client_secret_expires_at,
    )
    return await get_credentials(company_id)


async def delete_credentials(company_id: int) -> None:
    await m365_repo.delete_credentials(company_id)


async def _exchange_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str | None,
    scope: str | None = None,
) -> tuple[str, str | None, datetime | None]:
    token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    effective_scope = scope or _GRAPH_SCOPE
    data: dict[str, Any]
    if refresh_token:
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": f"{effective_scope} offline_access",
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    else:
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": effective_scope,
            "grant_type": "client_credentials",
        }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(token_endpoint, data=data)
    if response.status_code != 200:
        grant_type = "refresh_token" if refresh_token else "client_credentials"
        log_error(
            "Failed to acquire Microsoft 365 token",
            tenant_id=tenant_id,
            client_id=client_id,
            grant_type=grant_type,
            status=response.status_code,
            body=response.text,
        )
        raise M365Error("Unable to acquire Microsoft 365 access token")

    payload = response.json()
    access_token = str(payload.get("access_token"))
    new_refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(
            seconds=float(expires_in)
        )
    return access_token, str(new_refresh) if new_refresh else None, expires_at


async def acquire_access_token(
    company_id: int, *, force_client_credentials: bool = False
) -> str:
    creds = await get_credentials(company_id)
    if not creds:
        raise M365Error("Microsoft 365 credentials have not been configured")

    tenant_id = str(creds.get("tenant_id") or "").strip()
    client_id = str(creds.get("client_id") or "").strip()

    # Reuse a stored token that is still valid (with a 5-minute safety margin).
    # This avoids an unnecessary round-trip to Microsoft's token endpoint on
    # every call (e.g. after an app restart) and prevents transient failures
    # from breaking sync jobs when a perfectly valid token is already cached.
    #
    # For flows that explicitly require application permissions (for example
    # mailbox reporting APIs), callers can set ``force_client_credentials=True``
    # to bypass the cached delegated token and force an app-only token refresh.
    stored_token = creds.get("access_token")
    stored_expires_at = creds.get("token_expires_at")
    if not force_client_credentials and stored_token and stored_expires_at:
        # token_expires_at is stored as a naive UTC datetime; compare likewise.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        margin = timedelta(minutes=5)
        if (
            isinstance(stored_expires_at, datetime)
            and stored_expires_at - margin > now_utc
        ):
            log_info(
                "M365 using cached access token",
                company_id=company_id,
                tenant_id=tenant_id,
                client_id=client_id,
                token_expires_at=str(stored_expires_at),
            )
            return stored_token

    # Prefer the company's mapped CSP tenant ID when available.  This ensures
    # that a shared CSP admin app (registered in the partner/parent tenant) still
    # acquires a token scoped to the *customer* tenant rather than the parent,
    # which would otherwise cause /subscribedSkus to return the parent's licenses.
    csp_tenant_id = await companies_repo.get_company_csp_tenant_id(company_id)
    effective_tenant_id = csp_tenant_id or tenant_id
    csp_mapping_applied = bool(csp_tenant_id)

    log_info(
        "M365 acquiring access token",
        company_id=company_id,
        tenant_id=tenant_id,
        client_id=client_id,
        effective_tenant_id=effective_tenant_id,
        csp_mapping_applied=csp_mapping_applied,
    )

    stored_refresh = None if force_client_credentials else creds.get("refresh_token")
    grant_type = "refresh_token" if stored_refresh else "client_credentials"
    try:
        access_token, refresh, expires_at = await _exchange_token(
            tenant_id=effective_tenant_id,
            client_id=client_id,
            client_secret=creds.get("client_secret") or "",
            refresh_token=stored_refresh,
        )
    except M365Error:
        if not stored_refresh:
            raise
        # The stored refresh token is stale or revoked.  Fall back to the
        # client_credentials grant so that background sync jobs can continue
        # using application permissions without user interaction.
        log_error(
            "M365 refresh token is invalid; falling back to client_credentials grant",
            company_id=company_id,
            tenant_id=effective_tenant_id,
            client_id=client_id,
        )
        access_token, refresh, expires_at = await _exchange_token(
            tenant_id=effective_tenant_id,
            client_id=client_id,
            client_secret=creds.get("client_secret") or "",
            refresh_token=None,
        )
        grant_type = "client_credentials"
        # Clear the stale refresh token so future calls use client_credentials
        # immediately rather than attempting the refresh_token grant again.
        refresh = None

    log_info(
        "M365 access token acquired successfully",
        company_id=company_id,
        effective_tenant_id=effective_tenant_id,
        client_id=client_id,
        grant_type=grant_type,
    )

    expires_value = None
    if expires_at:
        expires_value = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    await m365_repo.update_tokens(
        company_id=company_id,
        refresh_token=_encrypt(refresh),
        access_token=_encrypt(access_token),
        token_expires_at=expires_value,
    )
    return access_token


async def _acquire_exo_access_token(company_id: int) -> tuple[str, str]:
    """Acquire an app-only access token for the Exchange Online PowerShell REST API.

    Uses the ``client_credentials`` grant with the Exchange Online scope
    (``https://outlook.office365.com/.default``).  The provisioned app must have
    the ``Exchange.ManageAsApp`` application permission and be assigned an
    appropriate Exchange RBAC role (e.g. Exchange Administrator) in the tenant.

    :returns: A tuple of ``(access_token, effective_tenant_id)``.
    """
    creds = await get_credentials(company_id)
    if not creds:
        raise M365Error("Microsoft 365 credentials have not been configured")

    tenant_id = str(creds.get("tenant_id") or "").strip()
    client_id = str(creds.get("client_id") or "").strip()

    csp_tenant_id = await companies_repo.get_company_csp_tenant_id(company_id)
    effective_tenant_id = csp_tenant_id or tenant_id

    access_token, _, _ = await _exchange_token(
        tenant_id=effective_tenant_id,
        client_id=client_id,
        client_secret=creds.get("client_secret") or "",
        refresh_token=None,
        scope=_EXO_SCOPE,
    )
    return access_token, effective_tenant_id


async def _graph_get(
    access_token: str,
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    req_headers: dict[str, str] = {"Authorization": f"Bearer {access_token}"}
    if extra_headers:
        req_headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=req_headers)
    if response.status_code != 200:
        log_error(
            "Microsoft Graph request failed",
            url=url,
            status=response.status_code,
            body=response.text,
        )
        raise M365Error(
            f"Microsoft Graph request failed ({response.status_code})",
            http_status=response.status_code,
        )
    return response.json()


async def _graph_get_all(access_token: str, url: str) -> list[dict[str, Any]]:
    """GET a Microsoft Graph collection endpoint, following ``@odata.nextLink`` pagination.

    Many Graph list endpoints (e.g. conditionalAccessPolicies,
    deviceCompliancePolicies) return a single page of results with an
    ``@odata.nextLink`` property pointing to the next page.  Callers that only
    fetch the first page may miss resources and produce incorrect results.  This
    helper transparently fetches all pages and returns the combined ``value`` list.
    """
    items: list[dict[str, Any]] = []
    while url:
        data = await _graph_get(access_token, url)
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return items


async def _graph_post(
    access_token: str,
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code not in (200, 201, 204):
        log_error(
            "Microsoft Graph POST failed",
            url=url,
            status=response.status_code,
            body=response.text,
        )
        raise M365Error(
            f"Microsoft Graph POST failed ({response.status_code})",
            http_status=response.status_code,
        )
    if response.status_code == 204:
        return {}
    return response.json()


async def _graph_delete(access_token: str, url: str) -> None:
    """Issue a DELETE request to Microsoft Graph.  Raises :exc:`M365Error` on failure."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.delete(url, headers=headers)
    if response.status_code not in (200, 204):
        log_error(
            "Microsoft Graph DELETE failed",
            url=url,
            status=response.status_code,
            body=response.text,
        )
        raise M365Error(
            f"Microsoft Graph DELETE failed ({response.status_code})",
            http_status=response.status_code,
        )


async def _delete_existing_apps_by_display_name(
    access_token: str,
    display_name: str,
) -> None:
    """Delete all app registrations whose ``displayName`` matches *display_name*.

    Searching by display name covers the case where a previous provision run
    left behind an orphaned app registration (e.g. if the stored
    ``app_object_id`` is stale or was never recorded).  Deletion of the app
    registration also removes the corresponding service principal in the same
    tenant.

    Errors are logged but never re-raised so that the caller (the provision
    flow) can continue to create a fresh registration even when cleanup fails.
    """
    safe_name = display_name.replace("'", "''")
    try:
        existing = await _graph_get(
            access_token,
            f"https://graph.microsoft.com/v1.0/applications"
            f"?$filter=displayName eq '{safe_name}'&$select=id,appId,displayName",
        )
    except M365Error as exc:
        log_error(
            "Failed to search for existing app registrations; skipping cleanup",
            display_name=display_name,
            error=str(exc),
        )
        return

    for app in existing.get("value", []):
        obj_id = app.get("id", "")
        app_id = app.get("appId", "")
        if not obj_id:
            continue
        try:
            await _graph_delete(
                access_token,
                f"https://graph.microsoft.com/v1.0/applications/{obj_id}",
            )
            log_info(
                "Deleted existing app registration before re-provisioning",
                app_object_id=obj_id,
                app_id=app_id,
                display_name=display_name,
            )
        except M365Error as exc:
            log_error(
                "Failed to delete existing app registration; continuing with provisioning",
                app_object_id=obj_id,
                app_id=app_id,
                error=str(exc),
            )


async def provision_app_registration(
    *,
    access_token: str,
    display_name: str = "MyPortal Integration",
    redirect_uri: str | None = None,
) -> dict[str, Any]:
    """Create a per-tenant app registration with required permissions.

    Uses a delegated *access_token* obtained via the admin-consent OAuth flow to:
    1. Create an App Registration in the tenant.
    2. Create the corresponding Service Principal (Enterprise App).
    3. Find the Microsoft Graph service principal in the tenant.
    4. Grant admin consent for each required application permission.
    5. Make the service principal an owner of the app registration so it can
       renew its own client secret later (requires Application.ReadWrite.OwnedBy).
    6. Generate and return a client secret for the new app.

    Returns a dict with ``client_id``, ``client_secret``, ``app_object_id``,
    ``client_secret_key_id`` and ``client_secret_expires_at``.  The client
    secret is returned in plain text exactly once and must be stored immediately.

    :param redirect_uri: The OAuth redirect URI to register on the app
        registration.  This must match the ``redirect_uri`` used in the
        connect flow so that Microsoft accepts the authorisation request.
        Should be an HTTPS URL.
    """
    settings = get_settings()
    secret_lifetime_days = settings.m365_client_secret_lifetime_days

    # 0. Remove any existing app registrations with the same display name so
    #    that re-provisioning always starts from a clean slate.
    await _delete_existing_apps_by_display_name(access_token, display_name)

    # 1. Create the app registration
    app_payload: dict[str, Any] = {
        "displayName": display_name,
        "signInAudience": "AzureADMyOrg",
        "requiredResourceAccess": [
            {
                "resourceAppId": _GRAPH_APP_ID,
                "resourceAccess": [
                    {"id": role_id, "type": "Role"} for role_id in _PROVISION_APP_ROLES
                ],
            },
            {
                "resourceAppId": _EXO_APP_ID,
                "resourceAccess": [
                    {"id": _EXO_MANAGE_AS_APP_ROLE, "type": "Role"},
                ],
            },
        ],
    }
    if redirect_uri:
        app_payload["web"] = {"redirectUris": [redirect_uri]}
    app_data = await _graph_post(
        access_token,
        "https://graph.microsoft.com/v1.0/applications",
        app_payload,
    )
    app_object_id: str = app_data["id"]
    client_id: str = app_data["appId"]
    log_info("Provisioned M365 app registration", client_id=client_id)

    # 2. Create a service principal (Enterprise App) for the registration
    sp_data = await _graph_post(
        access_token,
        "https://graph.microsoft.com/v1.0/servicePrincipals",
        {"appId": client_id},
    )
    sp_object_id: str = sp_data["id"]
    log_info("Created M365 service principal", sp_object_id=sp_object_id)

    # 3. Locate the Microsoft Graph service principal in this tenant
    graph_sp_response = await _graph_get(
        access_token,
        f"https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=appId eq '{_GRAPH_APP_ID}'&$select=id",
    )
    graph_sp_list = graph_sp_response.get("value", [])
    if not graph_sp_list:
        raise M365Error(
            "Unable to locate Microsoft Graph service principal in the tenant"
        )
    graph_sp_id: str = graph_sp_list[0]["id"]

    # 4. Grant admin consent for each required application permission.
    # 409 Conflict means the assignment already exists (e.g. an earlier partial
    # provision or Microsoft Graph eventual-consistency behaviour) – treat it as
    # success and continue so that the remaining roles are still processed.
    for role_id in _PROVISION_APP_ROLES:
        try:
            await _graph_post(
                access_token,
                f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
                {
                    "principalId": sp_object_id,
                    "resourceId": graph_sp_id,
                    "appRoleId": role_id,
                },
            )
        except M365Error as exc:
            if exc.http_status == 409:
                log_info(
                    "App role assignment already exists, skipping",
                    role_id=role_id,
                    sp_object_id=sp_object_id,
                )
            else:
                raise
    log_info(
        "Granted admin consent for provisioned M365 app",
        sp_object_id=sp_object_id,
    )

    # 4b. Grant Exchange Online Exchange.ManageAsApp role (best-effort).
    # This enables Get-MailboxPermission via the Exchange Online PowerShell
    # REST API.  The grant is non-fatal so provisioning still succeeds even
    # when the Exchange Online service principal is absent from the tenant.
    try:
        exo_sp_response = await _graph_get(
            access_token,
            f"https://graph.microsoft.com/v1.0/servicePrincipals"
            f"?$filter=appId eq '{_EXO_APP_ID}'&$select=id",
        )
        exo_sp_list = exo_sp_response.get("value", [])
        if exo_sp_list:
            exo_sp_id: str = exo_sp_list[0]["id"]
            try:
                await _graph_post(
                    access_token,
                    f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
                    {
                        "principalId": sp_object_id,
                        "resourceId": exo_sp_id,
                        "appRoleId": _EXO_MANAGE_AS_APP_ROLE,
                    },
                )
                log_info(
                    "Granted Exchange.ManageAsApp role",
                    sp_object_id=sp_object_id,
                )
            except M365Error as exc:
                if exc.http_status == 409:
                    log_info(
                        "Exchange.ManageAsApp role already assigned, skipping",
                        sp_object_id=sp_object_id,
                    )
                else:
                    log_error(
                        "Failed to grant Exchange.ManageAsApp role; "
                        "Get-MailboxPermission will not be available",
                        error=str(exc),
                    )
        else:
            log_info(
                "Exchange Online service principal not found in tenant; "
                "skipping Exchange.ManageAsApp role grant",
            )
    except M365Error as exc:
        log_error(
            "Failed to look up Exchange Online service principal; "
            "Get-MailboxPermission will not be available",
            error=str(exc),
        )

    # 5. Add the service principal as an owner of the app registration so it
    #    can call addPassword on itself (Application.ReadWrite.OwnedBy requires ownership).
    try:
        await _graph_post(
            access_token,
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/owners/$ref",
            {
                "@odata.id": (
                    f"https://graph.microsoft.com/v1.0/directoryObjects/{sp_object_id}"
                )
            },
        )
        log_info(
            "Added service principal as owner of M365 app registration",
            app_object_id=app_object_id,
            sp_object_id=sp_object_id,
        )
    except M365Error as exc:
        # Non-fatal: the app will still work but automatic secret renewal won't
        # be available until this is resolved.
        log_error(
            "Failed to add SP as owner of M365 app registration; "
            "automatic secret renewal will not be available",
            app_object_id=app_object_id,
            error=str(exc),
        )

    # 6. Create a client secret with a configurable lifetime (default: 730 days / 2 years)
    secret_expiry_date = date.today() + timedelta(days=secret_lifetime_days)
    secret_expiry_str = secret_expiry_date.isoformat() + "T00:00:00Z"
    secret_data = await _graph_post(
        access_token,
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/addPassword",
        {
            "passwordCredential": {
                "displayName": "MyPortal",
                "endDateTime": secret_expiry_str,
            }
        },
    )
    client_secret: str = secret_data["secretText"]
    client_secret_key_id: str | None = secret_data.get("keyId")
    client_secret_expires_at = datetime(
        secret_expiry_date.year,
        secret_expiry_date.month,
        secret_expiry_date.day,
    )
    log_info(
        "Created client secret for provisioned M365 app",
        client_id=client_id,
        expires_at=secret_expiry_str,
    )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "app_object_id": app_object_id,
        "client_secret_key_id": client_secret_key_id,
        "client_secret_expires_at": client_secret_expires_at,
    }


async def renew_client_secret(company_id: int) -> None:
    """Renew the Azure AD client secret for a provisioned M365 integration app.

    Authenticates using the provisioned app's own credentials via the
    ``client_credentials`` grant, then calls ``addPassword`` on the app
    registration to create a new secret.  The old secret is revoked after the
    new one has been safely persisted.

    Requires the provisioned app to:
    - Have ``Application.ReadWrite.OwnedBy`` application permission granted.
    - Be registered as an owner of its own app registration.

    Both of these are configured automatically by :func:`provision_app_registration`
    for apps provisioned after this feature was introduced.

    Raises :class:`M365Error` if the credentials are missing or the app object ID
    has not been stored (apps provisioned before this feature require re-provisioning).
    """
    settings = get_settings()
    creds = await get_credentials(company_id)
    if not creds:
        raise M365Error("No M365 credentials found for company")

    app_object_id = creds.get("app_object_id")
    if not app_object_id:
        raise M365Error(
            "App object ID not stored – re-provisioning is required to enable "
            "automatic client secret renewal for this company"
        )

    # Get an access token using the provisioned app's own client credentials.
    # refresh_token=None forces the client_credentials grant which returns a
    # token with all granted application permissions including
    # Application.ReadWrite.OwnedBy.
    access_token, _, _ = await _exchange_token(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds.get("client_secret") or "",
        refresh_token=None,
    )

    # Calculate new expiry
    secret_lifetime_days = settings.m365_client_secret_lifetime_days
    new_expiry_date = date.today() + timedelta(days=secret_lifetime_days)
    new_expiry_str = new_expiry_date.isoformat() + "T00:00:00Z"

    # Create new client secret via Graph API
    secret_data = await _graph_post(
        access_token,
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/addPassword",
        {
            "passwordCredential": {
                "displayName": "MyPortal",
                "endDateTime": new_expiry_str,
            }
        },
    )
    new_secret: str = secret_data["secretText"]
    new_key_id: str | None = secret_data.get("keyId")
    new_expires_at = datetime(
        new_expiry_date.year, new_expiry_date.month, new_expiry_date.day
    )

    # Save old key ID before updating so we can revoke it afterwards
    old_key_id: str | None = creds.get("client_secret_key_id")

    # Persist new secret – do this BEFORE revoking old key so we never lose access
    await m365_repo.update_client_secret(
        company_id=company_id,
        client_secret=_encrypt(new_secret),
        key_id=new_key_id,
        expires_at=new_expires_at,
    )
    log_info(
        "Renewed M365 client secret",
        company_id=company_id,
        new_key_id=new_key_id,
        expires_at=new_expiry_str,
    )

    # Revoke the old secret now that the new one is safely stored
    if old_key_id:
        try:
            await _graph_post(
                access_token,
                f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/removePassword",
                {"keyId": old_key_id},
            )
            log_info(
                "Revoked old M365 client secret",
                company_id=company_id,
                old_key_id=old_key_id,
            )
        except M365Error as exc:
            # Non-fatal: old secret will expire naturally; log for admin visibility
            log_error(
                "Failed to revoke old M365 client secret",
                company_id=company_id,
                old_key_id=old_key_id,
                error=str(exc),
            )


async def renew_expiring_client_secrets() -> dict[str, Any]:
    """Check all stored M365 credentials and renew any secrets expiring soon.

    A secret is considered "expiring soon" if its ``client_secret_expires_at``
    is within the configured renewal window
    (``M365_CLIENT_SECRET_RENEWAL_DAYS``, default 14 days).

    Returns a summary dict with ``renewed``, ``skipped``, and ``failed`` counts.
    """
    settings = get_settings()
    renewal_days = settings.m365_client_secret_renewal_days
    cutoff = datetime.utcnow() + timedelta(days=renewal_days)

    expiring = await m365_repo.list_credentials_expiring_before(cutoff)

    renewed = 0
    skipped = 0
    failed = 0

    for cred in expiring:
        company_id = int(cred["company_id"])
        if not cred.get("app_object_id"):
            log_error(
                "Skipping M365 secret renewal – app_object_id not stored; "
                "re-provisioning required",
                company_id=company_id,
            )
            skipped += 1
            continue
        try:
            await renew_client_secret(company_id)
            renewed += 1
        except M365Error as exc:
            log_error(
                "Failed to renew M365 client secret",
                company_id=company_id,
                error=str(exc),
            )
            failed += 1

    # Also check the admin app credential (auto-provisioned only)
    try:
        admin_creds = await get_admin_m365_credentials()
        if admin_creds and admin_creds.get("app_object_id"):
            raw_expiry = admin_creds.get("client_secret_expires_at")
            expiry_dt: datetime | None = None
            if raw_expiry:
                if isinstance(raw_expiry, datetime):
                    expiry_dt = raw_expiry
                else:
                    try:
                        expiry_dt = datetime.fromisoformat(str(raw_expiry))
                    except (ValueError, TypeError):
                        expiry_dt = None
            if expiry_dt and expiry_dt <= cutoff:
                try:
                    await renew_admin_client_secret()
                    renewed += 1
                    log_info("Renewed M365 admin client secret during scheduled check")
                except M365Error as exc:
                    log_error(
                        "Failed to renew M365 admin client secret",
                        error=str(exc),
                    )
                    failed += 1
    except Exception as exc:
        log_error("Error checking M365 admin credentials for renewal", error=str(exc))

    return {"renewed": renewed, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# CSP / Lighthouse admin app provisioning and renewal
# ---------------------------------------------------------------------------


async def provision_csp_admin_app_registration(
    *,
    access_token: str,
    tenant_id: str,
    display_name: str = "MyPortal CSP Admin",
    redirect_uri: str | None = None,
) -> dict[str, Any]:
    """Provision an app registration in the partner tenant for CSP/Lighthouse.

    Uses a delegated *access_token* obtained via the admin-consent OAuth flow to:
    1. Create an App Registration with delegated ``Directory.Read.All`` and
       application ``Application.ReadWrite.OwnedBy`` permissions.
    2. Create the corresponding Service Principal (Enterprise App).
    3. Grant admin consent for ``Directory.Read.All`` and
       ``Application.ReadWrite.OwnedBy`` application permissions.
    4. Add the service principal as an owner of the app registration so it can
       renew its own client secret later.
    5. Generate and return a client secret.

    Returns a dict with ``client_id``, ``client_secret``, ``app_object_id``,
    ``client_secret_key_id``, ``client_secret_expires_at``, and ``tenant_id``.
    The client secret is returned in plain text exactly once and must be stored
    immediately.

    :param redirect_uri: The OAuth redirect URI to register on the app
        registration.  This must match the ``redirect_uri`` used in the
        CSP sign-in flow so that Microsoft accepts the login request.
    """
    settings = get_settings()
    secret_lifetime_days = settings.m365_client_secret_lifetime_days

    # 0. Remove any existing app registrations with the same display name so
    #    that re-provisioning always starts from a clean slate.
    await _delete_existing_apps_by_display_name(access_token, display_name)

    # 1. Create the app registration with required permissions
    app_payload: dict[str, Any] = {
        "displayName": display_name,
        "signInAudience": "AzureADMyOrg",
        "requiredResourceAccess": [
            {
                "resourceAppId": _GRAPH_APP_ID,
                "resourceAccess": [
                    # Delegated Directory.Read.All (for CSP sign-in by partner admins)
                    {"id": _DIRECTORY_READ_ALL_SCOPE_ID, "type": "Scope"},
                    # Application permissions (granted below)
                    *[
                        {"id": role_id, "type": "Role"}
                        for role_id in _CSP_ADMIN_APP_ROLES
                    ],
                ],
            }
        ],
    }
    if redirect_uri:
        app_payload["web"] = {"redirectUris": [redirect_uri]}
    app_data = await _graph_post(
        access_token,
        "https://graph.microsoft.com/v1.0/applications",
        app_payload,
    )
    app_object_id: str = app_data["id"]
    client_id: str = app_data["appId"]
    log_info("Provisioned M365 CSP admin app registration", client_id=client_id)

    # 2. Create a service principal (Enterprise App) for the registration
    sp_data = await _graph_post(
        access_token,
        "https://graph.microsoft.com/v1.0/servicePrincipals",
        {"appId": client_id},
    )
    sp_object_id: str = sp_data["id"]
    log_info("Created M365 CSP admin service principal", sp_object_id=sp_object_id)

    # 3. Locate the Microsoft Graph service principal in this tenant
    graph_sp_response = await _graph_get(
        access_token,
        f"https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=appId eq '{_GRAPH_APP_ID}'&$select=id",
    )
    graph_sp_list = graph_sp_response.get("value", [])
    if not graph_sp_list:
        raise M365Error(
            "Unable to locate Microsoft Graph service principal in the tenant"
        )
    graph_sp_id: str = graph_sp_list[0]["id"]

    # 4. Grant admin consent for application permissions
    for role_id in _CSP_ADMIN_APP_ROLES:
        await _graph_post(
            access_token,
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
            {
                "principalId": sp_object_id,
                "resourceId": graph_sp_id,
                "appRoleId": role_id,
            },
        )
    log_info(
        "Granted admin consent for M365 CSP admin app",
        sp_object_id=sp_object_id,
    )

    # 5. Add the service principal as an owner so it can renew its own secret
    try:
        await _graph_post(
            access_token,
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/owners/$ref",
            {
                "@odata.id": (
                    f"https://graph.microsoft.com/v1.0/directoryObjects/{sp_object_id}"
                )
            },
        )
        log_info(
            "Added service principal as owner of M365 CSP admin app registration",
            app_object_id=app_object_id,
            sp_object_id=sp_object_id,
        )
    except M365Error as exc:
        log_error(
            "Failed to add SP as owner of M365 CSP admin app; "
            "automatic secret renewal will not be available",
            app_object_id=app_object_id,
            error=str(exc),
        )

    # 6. Create a client secret with configurable lifetime (default: 730 days / 2 years)
    secret_expiry_date = date.today() + timedelta(days=secret_lifetime_days)
    secret_expiry_str = secret_expiry_date.isoformat() + "T00:00:00Z"
    secret_data = await _graph_post(
        access_token,
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/addPassword",
        {
            "passwordCredential": {
                "displayName": "MyPortal",
                "endDateTime": secret_expiry_str,
            }
        },
    )
    client_secret: str = secret_data["secretText"]
    client_secret_key_id: str | None = secret_data.get("keyId")
    client_secret_expires_at = datetime(
        secret_expiry_date.year,
        secret_expiry_date.month,
        secret_expiry_date.day,
    )
    log_info(
        "Created client secret for M365 CSP admin app",
        client_id=client_id,
        expires_at=secret_expiry_str,
    )

    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "app_object_id": app_object_id,
        "client_secret_key_id": client_secret_key_id,
        "client_secret_expires_at": client_secret_expires_at,
    }


async def get_admin_m365_credentials() -> dict[str, Any] | None:
    """Return the stored CSP/Lighthouse admin app credentials, or ``None``.

    Reads the ``m365-admin`` integration module settings.  The ``client_secret``
    field is decrypted if it was stored as ciphertext (auto-provisioned) or
    returned as-is if it is already plain text (manually configured).
    """
    module = await modules_repo.get_module(_M365_ADMIN_MODULE_SLUG)
    if not module:
        return None
    settings = module.get("settings") or {}
    client_id = str(settings.get("client_id") or "").strip() or None
    raw_secret = str(settings.get("client_secret") or "").strip() or None
    if not client_id or not raw_secret:
        return None
    # decrypt_secret returns the value unchanged if it is not in ciphertext format,
    # giving backward-compatibility with manually-configured plaintext secrets.
    client_secret = decrypt_secret(raw_secret)
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": str(settings.get("tenant_id") or "").strip() or None,
        "app_object_id": str(settings.get("app_object_id") or "").strip() or None,
        "client_secret_key_id": str(settings.get("client_secret_key_id") or "").strip()
        or None,
        "client_secret_expires_at": settings.get("client_secret_expires_at") or None,
    }


async def update_admin_m365_credentials(
    *,
    client_id: str,
    client_secret: str,
    tenant_id: str | None = None,
    app_object_id: str | None = None,
    client_secret_key_id: str | None = None,
    client_secret_expires_at: datetime | None = None,
) -> None:
    """Persist CSP/Lighthouse admin app credentials to the ``m365-admin`` module.

    The ``client_secret`` is encrypted before storage.  Any field that is
    ``None`` is omitted from the update so that existing values are preserved.
    """
    module = await modules_repo.get_module(_M365_ADMIN_MODULE_SLUG)
    existing_settings: dict[str, Any] = dict((module or {}).get("settings") or {})

    new_settings: dict[str, Any] = {
        **existing_settings,
        "client_id": client_id,
        "client_secret": encrypt_secret(client_secret),
    }
    if tenant_id is not None:
        new_settings["tenant_id"] = tenant_id
    if app_object_id is not None:
        new_settings["app_object_id"] = app_object_id
    if client_secret_key_id is not None:
        new_settings["client_secret_key_id"] = client_secret_key_id
    if client_secret_expires_at is not None:
        new_settings["client_secret_expires_at"] = client_secret_expires_at.isoformat()

    await modules_repo.update_module(
        _M365_ADMIN_MODULE_SLUG,
        enabled=True,
        settings=new_settings,
    )
    log_info(
        "Updated M365 admin credentials in integration module", client_id=client_id
    )


async def renew_admin_client_secret() -> None:
    """Renew the Azure AD client secret for the provisioned CSP/Lighthouse admin app.

    Authenticates using the admin app's own credentials via the
    ``client_credentials`` grant, then calls ``addPassword`` to create a new
    secret.  The old secret is revoked after the new one is safely persisted.

    Requires the provisioned admin app to have ``Application.ReadWrite.OwnedBy``
    application permission granted and to be registered as an owner of its own
    app registration (configured automatically by
    :func:`provision_csp_admin_app_registration`).

    Raises :class:`M365Error` if the credentials or ``app_object_id`` are missing.
    """
    settings_cfg = get_settings()
    creds = await get_admin_m365_credentials()
    if not creds:
        raise M365Error("No M365 admin credentials found")

    tenant_id = creds.get("tenant_id")
    if not tenant_id:
        raise M365Error(
            "Admin tenant ID not stored – re-provisioning is required to enable "
            "automatic client secret renewal for the admin app"
        )

    app_object_id = creds.get("app_object_id")
    if not app_object_id:
        raise M365Error(
            "App object ID not stored – re-provisioning is required to enable "
            "automatic client secret renewal for the admin app"
        )

    # Acquire an access token via client_credentials using the admin app's own credentials
    access_token, _, _ = await _exchange_token(
        tenant_id=tenant_id,
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        refresh_token=None,
    )

    # Calculate new expiry
    secret_lifetime_days = settings_cfg.m365_client_secret_lifetime_days
    new_expiry_date = date.today() + timedelta(days=secret_lifetime_days)
    new_expiry_str = new_expiry_date.isoformat() + "T00:00:00Z"

    # Create new client secret via Graph API
    secret_data = await _graph_post(
        access_token,
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/addPassword",
        {
            "passwordCredential": {
                "displayName": "MyPortal",
                "endDateTime": new_expiry_str,
            }
        },
    )
    new_secret: str = secret_data["secretText"]
    new_key_id: str | None = secret_data.get("keyId")
    new_expires_at = datetime(
        new_expiry_date.year, new_expiry_date.month, new_expiry_date.day
    )

    old_key_id: str | None = creds.get("client_secret_key_id")

    # Persist new secret BEFORE revoking old key so we never lose access
    await update_admin_m365_credentials(
        client_id=creds["client_id"],
        client_secret=new_secret,
        tenant_id=tenant_id,
        app_object_id=app_object_id,
        client_secret_key_id=new_key_id,
        client_secret_expires_at=new_expires_at,
    )
    log_info(
        "Renewed M365 admin client secret",
        new_key_id=new_key_id,
        expires_at=new_expiry_str,
    )

    # Revoke the old secret now that the new one is safely stored
    if old_key_id:
        try:
            await _graph_post(
                access_token,
                f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/removePassword",
                {"keyId": old_key_id},
            )
            log_info("Revoked old M365 admin client secret", old_key_id=old_key_id)
        except M365Error as exc:
            log_error(
                "Failed to revoke old M365 admin client secret",
                old_key_id=old_key_id,
                error=str(exc),
            )


async def _sync_staff_assignments(
    *,
    company_id: int,
    license_id: int,
    access_token: str,
    sku_id: str,
) -> None:
    # Filtering by assignedLicenses is an advanced OData query that requires
    # the ConsistencyLevel: eventual header and $count=true parameter.
    # Without these, Microsoft Graph returns a 400 Bad Request.
    # The ConsistencyLevel: eventual header must also be forwarded on every
    # @odata.nextLink paginated request, otherwise subsequent pages return 403.
    log_info(
        "M365 syncing staff assignments for license",
        company_id=company_id,
        license_id=license_id,
        sku_id=sku_id,
    )
    url: str | None = (
        "https://graph.microsoft.com/v1.0/users?"
        f"$filter=assignedLicenses/any(x:x/skuId eq {sku_id})&"
        "$select=id,displayName,mail,userPrincipalName,givenName,surname,signInActivity&"
        "$count=true"
    )
    consistency_headers = {"ConsistencyLevel": "eventual"}
    assigned_emails: set[str] = set()
    while url:
        payload = await _graph_get(
            access_token,
            url,
            extra_headers=consistency_headers,
        )
        for user in payload.get("value", []):
            email = (
                (user.get("mail") or user.get("userPrincipalName") or "")
                .strip()
                .lower()
            )
            if not email:
                continue
            sign_in_activity = user.get("signInActivity") or {}
            last_sign_in_str = sign_in_activity.get("lastSignInDateTime")
            last_sign_in = parse_graph_datetime(last_sign_in_str)
            staff = await staff_repo.get_staff_by_company_and_email(company_id, email)
            if not staff:
                first = (user.get("givenName") or "").strip() or "Unknown"
                last = (
                    (user.get("surname") or "").strip() or user.get("displayName") or ""
                )
                created = await staff_repo.create_staff(
                    company_id=company_id,
                    first_name=first or "Unknown",
                    last_name=last or "",
                    email=email,
                    mobile_phone=None,
                    date_onboarded=None,
                    date_offboarded=None,
                    enabled=True,
                    street=None,
                    city=None,
                    state=None,
                    postcode=None,
                    country=None,
                    department=None,
                    job_title=None,
                    org_company=None,
                    manager_name=None,
                    account_action=None,
                    syncro_contact_id=None,
                    source="m365",
                    m365_last_sign_in=last_sign_in,
                )
                staff = created
            elif last_sign_in is not None:
                await staff_repo.update_m365_last_sign_in(
                    int(staff["id"]), last_sign_in
                )
            assigned_emails.add(email)
            await license_repo.link_staff_to_license(int(staff["id"]), license_id)
        url = payload.get("@odata.nextLink")

    current_staff = await license_repo.list_staff_for_license(license_id)
    to_unlink = [
        int(member["id"])
        for member in current_staff
        if member.get("email") and member["email"].lower() not in assigned_emails
    ]
    await license_repo.bulk_unlink_staff(license_id, to_unlink)


async def sync_company_licenses(company_id: int) -> None:
    log_info("M365 starting license synchronisation", company_id=company_id)
    access_token = await acquire_access_token(company_id, force_client_credentials=True)
    payload = await _graph_get(
        access_token, "https://graph.microsoft.com/v1.0/subscribedSkus"
    )
    synced_skus: set[str] = set()
    for sku in payload.get("value", []):
        part_number = str(sku.get("skuPartNumber") or "").strip()
        sku_id = sku.get("skuId")
        prepaid = sku.get("prepaidUnits", {})
        count = int(prepaid.get("enabled") or 0)
        app = None
        if part_number:
            app = await apps_repo.get_app_by_vendor_sku(part_number)
        name = app.get("name") if app else part_number or "Unknown SKU"
        existing = await license_repo.get_license_by_company_and_sku(
            company_id, part_number
        )
        if existing:
            await license_repo.update_license(
                existing["id"],
                company_id=company_id,
                name=name,
                platform=part_number,
                count=count,
                expiry_date=existing.get("expiry_date"),
                contract_term=existing.get("contract_term"),
            )
            license_id = existing["id"]
        else:
            created = await license_repo.create_license(
                company_id=company_id,
                name=name,
                platform=part_number,
                count=count,
                expiry_date=None,
                contract_term="",
            )
            license_id = created["id"]
        if part_number:
            synced_skus.add(part_number)
        if sku_id:
            await _sync_staff_assignments(
                company_id=company_id,
                license_id=int(license_id),
                access_token=access_token,
                sku_id=str(sku_id),
            )
    today = date.today()
    all_licenses = await license_repo.list_company_licenses(company_id)
    for lic in all_licenses:
        sku = lic.get("platform") or ""
        expiry = lic.get("expiry_date")
        if isinstance(expiry, datetime):
            expiry_date_only = expiry.date()
        elif isinstance(expiry, date):
            expiry_date_only = expiry
        else:
            expiry_date_only = None
        is_stale = sku not in synced_skus
        is_expired = expiry_date_only is not None and expiry_date_only < today
        if is_stale or is_expired:
            reason = (
                "expired"
                if is_expired and not is_stale
                else (
                    "not_in_tenant"
                    if is_stale and not is_expired
                    else "stale_and_expired"
                )
            )
            await license_repo.delete_license(lic["id"])
            log_info(
                "M365 removed stale or expired license",
                company_id=company_id,
                license_id=lic["id"],
                platform=sku,
                reason=reason,
            )
    log_info("Microsoft 365 license synchronisation completed", company_id=company_id)


async def test_connectivity(company_id: int) -> dict[str, Any]:
    """Validate stored credentials can acquire a token and call Microsoft Graph."""
    access_token = await acquire_access_token(company_id)
    payload = await _graph_get(
        access_token,
        "https://graph.microsoft.com/v1.0/organization?$select=id,displayName",
    )
    organization = (payload.get("value") or [{}])[0]
    return {
        "graph_access": True,
        "organization_id": str(organization.get("id") or "").strip() or None,
        "organization_name": str(organization.get("displayName") or "").strip() or None,
    }


async def get_all_users(company_id: int) -> list[dict[str, Any]]:
    """Return all M365 users for the given company, including disabled accounts.

    Fetches members from the Microsoft Graph ``/users`` endpoint and handles
    ``@odata.nextLink`` pagination so that tenants with more than the default
    page size are fully returned.

    The returned user objects include ``accountEnabled`` so callers can
    distinguish active users from blocked/disabled (ex-staff) accounts.
    """
    access_token = await acquire_access_token(company_id)
    url = (
        "https://graph.microsoft.com/v1.0/users?"
        "$select=id,displayName,mail,userPrincipalName,givenName,surname,"
        "mobilePhone,businessPhones,streetAddress,city,state,postalCode,country,"
        "department,jobTitle,signInActivity,accountEnabled"
    )
    users: list[dict[str, Any]] = []
    while url:
        payload = await _graph_get(access_token, url)
        users.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
    return users


async def _exchange_obo_token(
    *,
    customer_tenant_id: str,
    client_id: str,
    client_secret: str,
    user_assertion: str,
) -> tuple[str, str | None, datetime | None]:
    """Exchange a partner admin token for a customer-tenant-scoped token via OBO.

    Uses the OAuth2 On-Behalf-Of (OBO) flow to exchange the partner admin's
    access token for a delegated token scoped to the customer tenant.  This
    enables GDAP delegated-admin operations (such as granting app permissions)
    in the customer tenant.

    :param customer_tenant_id: The Azure AD tenant ID of the customer.
    :param client_id: The CSP admin app's client ID (in the partner tenant).
    :param client_secret: The CSP admin app's client secret.
    :param user_assertion: The partner admin's access token to exchange.
    """
    token_endpoint = (
        f"https://login.microsoftonline.com/{customer_tenant_id}/oauth2/v2.0/token"
    )
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": client_id,
        "client_secret": client_secret,
        "assertion": user_assertion,
        "scope": _GRAPH_SCOPE,
        "requested_token_use": "on_behalf_of",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(token_endpoint, data=data)
    if response.status_code != 200:
        log_error(
            "OBO token exchange failed",
            status=response.status_code,
            body=response.text,
        )
        raise M365Error(
            f"OBO token exchange failed ({response.status_code}): "
            f"{response.text[:200]}"
        )

    payload = response.json()
    access_token = str(payload.get("access_token"))
    new_refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
    return access_token, str(new_refresh) if new_refresh else None, expires_at


async def verify_tenant_permissions(
    company_id: int,
    csp_access_token: str | None = None,
) -> dict[str, Any]:
    """Verify that the provisioned M365 app has all required permissions.

    Uses the company's stored credentials (client_credentials grant) to check
    the current app role assignments on the service principal.  If any required
    permissions are missing and a CSP *access_token* is provided, attempts to
    grant them via the GDAP on-behalf-of flow using the stored admin app
    credentials.

    Returns a dict with:

    - ``all_ok`` – ``True`` if all required permissions are present
    - ``missing`` – list of role IDs that are missing
    - ``present`` – list of role IDs that are present
    - ``updated`` – ``True`` if missing permissions were successfully granted
    - ``error``   – human-readable error message if the check or update failed
    """
    creds = await get_credentials(company_id)
    if not creds:
        raise M365Error("No M365 credentials found for company")

    tenant_id = creds["tenant_id"]
    client_id = creds["client_id"]
    client_secret = creds.get("client_secret") or ""

    # Acquire a client_credentials access token for the customer tenant
    access_token, _, _ = await _exchange_token(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=None,
    )

    # Find the service principal for this app in the customer tenant
    sp_response = await _graph_get(
        access_token,
        f"https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=appId eq '{client_id}'&$select=id",
    )
    sp_list = sp_response.get("value", [])
    if not sp_list:
        raise M365Error("Service principal not found in tenant")
    sp_object_id: str = sp_list[0]["id"]

    # Retrieve current app role assignments for the service principal
    assignments_response = await _graph_get(
        access_token,
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
    )
    assigned_roles: set[str] = {
        str(a.get("appRoleId") or "") for a in assignments_response.get("value", [])
    }

    required_roles: set[str] = set(_PROVISION_APP_ROLES)
    present: list[str] = sorted(required_roles & assigned_roles)
    missing: list[str] = sorted(required_roles - assigned_roles)

    if not missing:
        return {"all_ok": True, "missing": [], "present": present, "updated": False}

    # No CSP session — report what is missing but cannot fix automatically
    if not csp_access_token:
        return {
            "all_ok": False,
            "missing": missing,
            "present": present,
            "updated": False,
        }

    # Exchange the CSP session token for a customer-tenant-scoped token via OBO
    admin_creds = await get_admin_m365_credentials()
    if not admin_creds:
        return {
            "all_ok": False,
            "missing": missing,
            "present": present,
            "updated": False,
            "error": "CSP admin credentials not configured",
        }

    try:
        customer_token, _, _ = await _exchange_obo_token(
            customer_tenant_id=tenant_id,
            client_id=admin_creds["client_id"],
            client_secret=admin_creds["client_secret"],
            user_assertion=csp_access_token,
        )
    except M365Error as exc:
        return {
            "all_ok": False,
            "missing": missing,
            "present": present,
            "updated": False,
            "error": f"Unable to obtain admin token for tenant: {exc}",
        }

    # Locate the Microsoft Graph service principal in the customer tenant
    graph_sp_response = await _graph_get(
        customer_token,
        f"https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=appId eq '{_GRAPH_APP_ID}'&$select=id",
    )
    graph_sp_list = graph_sp_response.get("value", [])
    if not graph_sp_list:
        return {
            "all_ok": False,
            "missing": missing,
            "present": present,
            "updated": False,
            "error": "Microsoft Graph service principal not found in customer tenant",
        }
    graph_sp_id: str = graph_sp_list[0]["id"]

    # Grant missing app role assignments
    for role_id in missing:
        try:
            await _graph_post(
                customer_token,
                f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
                {
                    "principalId": sp_object_id,
                    "resourceId": graph_sp_id,
                    "appRoleId": role_id,
                },
            )
        except M365Error as exc:
            return {
                "all_ok": False,
                "missing": missing,
                "present": present,
                "updated": False,
                "error": f"Failed to grant permission {role_id}: {exc}",
            }

    log_info(
        "Granted missing M365 permissions for tenant",
        company_id=company_id,
        tenant_id=tenant_id,
        granted_roles=missing,
    )
    return {
        "all_ok": True,
        "missing": [],
        "present": sorted(required_roles),
        "updated": True,
    }


async def try_grant_missing_permissions(
    company_id: int,
    access_token: str,
) -> bool:
    """Best-effort: grant any missing ``_PROVISION_APP_ROLES`` to the company's
    enterprise app service principal using the provided *access_token*.

    This is called from the "Authorize portal access" (``/m365/connect``)
    callback so that when an administrator re-authorises, any application
    permissions that were added after the app was originally provisioned (e.g.
    ``Reports.Read.All`` and ``MailboxSettings.Read`` for mailbox sync) are
    automatically added to the app's ``appRoleAssignments``.

    Returns ``True`` if one or more previously-missing permissions were
    successfully granted, ``False`` otherwise (no grants needed, or all
    attempts failed).  Failures are logged but never raised – the connect flow
    must not be interrupted by a permission-grant error.
    """
    creds = await get_credentials(company_id)
    if not creds:
        return False

    client_id = str(creds.get("client_id") or "").strip()
    if not client_id:
        return False

    try:
        # Find the service principal for the company's app
        sp_response = await _graph_get(
            access_token,
            "https://graph.microsoft.com/v1.0/servicePrincipals"
            f"?$filter=appId eq '{client_id}'&$select=id",
        )
        sp_list = sp_response.get("value", [])
        if not sp_list:
            log_info(
                "try_grant_missing_permissions: service principal not found",
                company_id=company_id,
                client_id=client_id,
            )
            return False
        sp_object_id: str = sp_list[0]["id"]

        # Retrieve current appRoleAssignments
        assignments_response = await _graph_get(
            access_token,
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
        )
        assigned_roles: set[str] = {
            str(a.get("appRoleId") or "") for a in assignments_response.get("value", [])
        }

        required_roles: set[str] = set(_PROVISION_APP_ROLES)
        missing: list[str] = sorted(required_roles - assigned_roles)
        exo_needed = _EXO_MANAGE_AS_APP_ROLE not in assigned_roles
        if not missing and not exo_needed:
            return False

        granted: list[str] = []

        # Grant each missing Graph role assignment
        if missing:
            # Locate the Microsoft Graph service principal in this tenant
            graph_sp_response = await _graph_get(
                access_token,
                "https://graph.microsoft.com/v1.0/servicePrincipals"
                f"?$filter=appId eq '{_GRAPH_APP_ID}'&$select=id",
            )
            graph_sp_list = graph_sp_response.get("value", [])
            if not graph_sp_list:
                log_info(
                    "try_grant_missing_permissions: Graph SP not found",
                    company_id=company_id,
                )
            else:
                graph_sp_id: str = graph_sp_list[0]["id"]
                for role_id in missing:
                    try:
                        await _graph_post(
                            access_token,
                            f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
                            {
                                "principalId": sp_object_id,
                                "resourceId": graph_sp_id,
                                "appRoleId": role_id,
                            },
                        )
                        granted.append(role_id)
                    except M365Error as exc:
                        log_error(
                            "try_grant_missing_permissions: failed to grant role",
                            company_id=company_id,
                            role_id=role_id,
                            error=str(exc),
                        )

            if granted:
                log_info(
                    "Granted missing M365 permissions via connect flow",
                    company_id=company_id,
                    granted_roles=granted,
                )

        # Best-effort: also grant Exchange.ManageAsApp if not already assigned.
        if exo_needed:
            try:
                exo_sp_response = await _graph_get(
                    access_token,
                    "https://graph.microsoft.com/v1.0/servicePrincipals"
                    f"?$filter=appId eq '{_EXO_APP_ID}'&$select=id",
                )
                exo_sp_list = exo_sp_response.get("value", [])
                if exo_sp_list:
                    exo_sp_id: str = exo_sp_list[0]["id"]
                    try:
                        await _graph_post(
                            access_token,
                            f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}/appRoleAssignments",
                            {
                                "principalId": sp_object_id,
                                "resourceId": exo_sp_id,
                                "appRoleId": _EXO_MANAGE_AS_APP_ROLE,
                            },
                        )
                        granted.append(_EXO_MANAGE_AS_APP_ROLE)
                        log_info(
                            "Granted Exchange.ManageAsApp via connect flow",
                            company_id=company_id,
                        )
                    except M365Error as exc:
                        if exc.http_status != 409:
                            log_error(
                                "try_grant_missing_permissions: "
                                "failed to grant Exchange.ManageAsApp",
                                company_id=company_id,
                                error=str(exc),
                            )
            except M365Error:
                pass  # Exchange Online SP lookup failed; non-fatal

        return bool(granted)
    except Exception as exc:  # noqa: BLE001
        log_error(
            "try_grant_missing_permissions: unexpected error",
            company_id=company_id,
            error=str(exc),
        )
        return False


async def ensure_service_principal_for_app(
    access_token: str, app_id: str
) -> dict[str, Any]:
    """Ensure an enterprise application (service principal) exists for ``app_id``.

    This is used by CSP/Lighthouse onboarding helpers so a Global Admin can
    bootstrap the enterprise app in a customer tenant without manual portal
    navigation.
    """
    clean_app_id = str(app_id or "").strip()
    if not clean_app_id:
        raise M365Error("Application ID is required")

    existing = await _graph_get(
        access_token,
        f"https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=appId eq '{clean_app_id}'&$select=id,appId,displayName",
    )
    existing_items = existing.get("value", [])
    if existing_items:
        return {
            "created": False,
            "service_principal": existing_items[0],
        }

    created = await _graph_post(
        access_token,
        "https://graph.microsoft.com/v1.0/servicePrincipals",
        {"appId": clean_app_id},
    )
    return {
        "created": True,
        "service_principal": created,
    }


async def list_csp_customers(access_token: str) -> list[dict[str, Any]]:
    """Return the list of customer tenants managed by the signed-in CSP/Lighthouse account.

    Calls ``GET /v1.0/contracts`` on Microsoft Graph, which requires the signed-in
    user to be a member of a CSP partner tenant with GDAP relationships or legacy
    delegated admin privileges.

    Each returned dict contains:
    - ``tenant_id``     – the customer's Azure AD tenant ID
    - ``name``          – the customer's display name
    - ``default_domain``– the customer's default domain name
    - ``contract_type`` – the contract type string from Graph (e.g. ``"Contract"``)
    """
    url = (
        "https://graph.microsoft.com/v1.0/contracts"
        "?$select=customerId,displayName,defaultDomainName,contractType"
    )
    customers: list[dict[str, Any]] = []
    while url:
        data = await _graph_get(access_token, url)
        for item in data.get("value", []):
            tenant_id = str(item.get("customerId") or "").strip()
            if not tenant_id:
                continue
            customers.append(
                {
                    "tenant_id": tenant_id,
                    "name": str(item.get("displayName") or "").strip(),
                    "default_domain": str(item.get("defaultDomainName") or "").strip(),
                    "contract_type": str(item.get("contractType") or "").strip(),
                }
            )
        url = data.get("@odata.nextLink")
    customers.sort(key=lambda c: c["name"].lower())
    return customers


async def _count_forwarding_rules(access_token: str, user_id: str) -> int:
    """Return the number of inbox message rules that forward or redirect mail.

    Queries the ``/mailFolders/inbox/messageRules`` endpoint for the given user
    and counts rules that have ``forwardTo``, ``redirectTo``, or
    ``forwardAsAttachmentTo`` actions populated.  Returns 0 if the endpoint is
    unavailable (e.g. the mailbox does not exist).

    A 403 (access denied) response is **re-raised** so that the caller can
    detect a missing ``MailboxSettings.Read`` permission and skip remaining
    users instead of repeating failing requests for every mailbox.
    """
    url = (
        f"https://graph.microsoft.com/v1.0/users/{user_id}"
        "/mailFolders/inbox/messageRules"
    )
    try:
        rules = await _graph_get_all(access_token, url)
    except M365Error as exc:
        if exc.http_status == 403:
            raise
        return 0
    count = 0
    for rule in rules:
        actions = rule.get("actions") or {}
        if (
            actions.get("forwardTo")
            or actions.get("redirectTo")
            or actions.get("forwardAsAttachmentTo")
        ):
            count += 1
    return count


async def _get_user_mail_enabled_groups(
    access_token: str, user_id: str
) -> list[dict[str, Any]]:
    """Return mail-enabled group memberships for a user.

    Queries ``/users/{id}/memberOf`` and filters to objects that have a
    non-empty ``mail`` property and ``mailEnabled == True``.  Returns an empty
    list if the request fails so callers can treat any failure as *no groups*.
    """
    url = (
        f"https://graph.microsoft.com/v1.0/users/{user_id}/memberOf"
        "?$select=id,displayName,mail,mailEnabled"
    )
    try:
        groups = await _graph_get_all(access_token, url)
    except M365Error:
        return []
    return [g for g in groups if g.get("mailEnabled") and g.get("mail")]


async def _get_mailbox_group_members(
    access_token: str, mailbox_email: str
) -> list[dict[str, Any]]:
    """Return user members of the M365 group backing the given mailbox.

    Looks up the unified group whose ``mail`` address matches *mailbox_email*,
    then fetches the direct members of that group.  Only ``#microsoft.graph.user``
    objects are returned; nested groups, service principals and other non-user
    member types are excluded.

    Returns an empty list when no backing group is found or when any request
    fails, so callers can treat any failure as *no members*.
    """
    # Find the unified group whose primary SMTP matches the mailbox address.
    group_filter_url = (
        "https://graph.microsoft.com/v1.0/groups"
        f"?$filter=mail eq '{mailbox_email}'&$select=id,displayName"
    )
    try:
        groups = await _graph_get_all(access_token, group_filter_url)
    except M365Error:
        return []

    if not groups:
        return []

    group_id = groups[0].get("id")
    if not group_id:
        return []

    # Fetch the direct members of the backing group.
    members_url = (
        f"https://graph.microsoft.com/v1.0/groups/{group_id}/members"
        "?$select=id,displayName,userPrincipalName,mail"
    )
    try:
        members = await _graph_get_all(access_token, members_url)
    except M365Error:
        return []

    # Return only user objects (exclude nested groups, service principals, etc.).
    return [m for m in members if m.get("userPrincipalName")]


async def _fetch_mailbox_usage_report(access_token: str) -> list[dict[str, Any]]:
    """Return mailbox usage entries from Microsoft Graph Reports API.

    Always uses the CSV export of ``getMailboxUsageDetail`` because the JSON
    projection (``$format=application/json``) omits the
    ``archiveMailboxStorageUsedInBytes`` field — archive mailbox size is only
    present in the CSV download.
    """

    def _normalise_report_item(item: dict[str, Any]) -> dict[str, Any] | None:
        def _normalise_key(key: str) -> str:
            return " ".join(
                str(key or "").replace("\ufeff", "").strip().lower().split()
            )

        def _parse_int(raw: Any, default: int = 0) -> int:
            value = str(raw or "").replace(",", "").strip()
            if not value:
                return default
            try:
                # Use float() as an intermediate step so that values returned
                # by the Graph CSV in floating-point notation
                # (e.g. "5368709120.0" or "5.37E+09") are handled correctly.
                # Plain integer strings are unaffected.
                return int(float(value))
            except (TypeError, ValueError):
                return default

        normalised_item = {_normalise_key(k): v for k, v in item.items()}

        upn = (
            str(
                item.get("userPrincipalName")
                or normalised_item.get("user principal name")
                or ""
            )
            .strip()
            .lower()
        )
        if not upn:
            return None

        display_name = str(
            item.get("displayName") or normalised_item.get("display name") or upn
        ).strip()
        storage_bytes = _parse_int(
            item.get("storageUsedInBytes")
            if "storageUsedInBytes" in item
            else normalised_item.get("storage used (byte)")
        )
        archive_bytes = _parse_int(
            item.get("archiveMailboxStorageUsedInBytes")
            if "archiveMailboxStorageUsedInBytes" in item
            else (
                normalised_item.get("archive mailbox storage used (byte)")
                if "archive mailbox storage used (byte)" in normalised_item
                else normalised_item.get("archive storage used (byte)")
            )
        )
        is_deleted_raw = (
            str(
                item.get("isDeleted")
                if "isDeleted" in item
                else normalised_item.get("is deleted") or "false"
            )
            .strip()
            .lower()
        )
        # "Has Archive" (True/False) is a dedicated column in the Graph CSV
        # report that indicates whether an online archive is provisioned,
        # regardless of whether it currently holds any data.
        has_archive_raw = (
            str(
                item.get("hasArchive")
                if "hasArchive" in item
                else normalised_item.get("has archive") or "false"
            )
            .strip()
            .lower()
        )
        return {
            "userPrincipalName": upn,
            "displayName": display_name,
            "storageUsedInBytes": storage_bytes,
            "archiveMailboxStorageUsedInBytes": archive_bytes,
            "hasArchive": has_archive_raw in {"true", "1", "yes"},
            "isDeleted": is_deleted_raw in {"true", "1", "yes"},
        }

    csv_report_url = (
        "https://graph.microsoft.com/v1.0/reports/" "getMailboxUsageDetail(period='D7')"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        # Reports endpoints normally return a temporary CSV download URL via
        # redirect. Handle the redirect manually so we can log and parse
        # deterministically.
        "Accept": "text/csv",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.get(csv_report_url, headers=headers)
        if response.status_code not in (302, 303, 307, 308):
            log_error(
                "Mailbox usage CSV export request failed",
                url=csv_report_url,
                status=response.status_code,
                body=response.text,
            )
            raise M365Error(
                f"Microsoft Graph request failed ({response.status_code})",
                http_status=response.status_code,
            )

        download_url = str(response.headers.get("Location") or "").strip()
        if not download_url:
            raise M365Error("Mailbox usage CSV export missing download URL")

        csv_response = await client.get(download_url)
        if csv_response.status_code != 200:
            log_error(
                "Mailbox usage CSV download failed",
                status=csv_response.status_code,
                body=csv_response.text,
            )
            raise M365Error(
                f"Microsoft Graph request failed ({csv_response.status_code})",
                http_status=csv_response.status_code,
            )

    csv_text = csv_response.text
    # Graph report downloads can be UTF-16 encoded without an explicit
    # charset. httpx then decodes as UTF-8 and leaves NUL bytes in-place,
    # which causes DictReader to miss headers/rows. Re-decode from raw bytes
    # when that pattern is detected.
    if "\x00" in csv_text:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                csv_text = csv_response.content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

    parsed_rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        filtered_row = {key: value for key, value in row.items() if key is not None}
        if not filtered_row:
            continue
        # Excel-style CSVs may include a dialect prefix row: "sep=,"
        if "sep=" in next(iter(filtered_row)).lower() and len(filtered_row) == 1:
            continue
        normalised_row = _normalise_report_item(filtered_row)
        if normalised_row is not None:
            parsed_rows.append(normalised_row)
    return parsed_rows


_DIRECT_MAILBOX_PERMISSION_SELF = "nt authority\\self"


def _normalise_direct_mailbox_permission_principal(user_value: str) -> tuple[str, str]:
    """Convert a mailboxPermission user value into display/upn fields."""
    candidate = str(user_value or "").strip()
    if not candidate:
        return "", ""

    match = re.search(
        r"([A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,})", candidate, re.IGNORECASE
    )
    if match:
        upn = match.group(1).lower()
        display_name = candidate.replace(match.group(1), "").strip(" <>()[]-	") or upn
        return display_name, upn

    lower_candidate = candidate.lower()
    return candidate, lower_candidate


def _parse_exo_mailbox_permission_records(
    mailbox_email: str,
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Parse Get-MailboxPermission response records into member dicts.

    Filters to FullAccess, non-deny, non-self entries and returns a list of
    ``{"member_display_name": ..., "member_upn": ...}`` dicts.
    """
    members: dict[str, dict[str, str]] = {}

    for record in records:
        access_rights_raw = record.get("AccessRights")
        if access_rights_raw is None:
            access_rights_raw = record.get("accessRights")
        if access_rights_raw is None:
            access_rights_raw = record.get("access_rights")
        # Handle nested object format: {"value": [...]} or {"@odata.type": ..., "value": [...]}
        if isinstance(access_rights_raw, dict):
            access_rights_raw = access_rights_raw.get("value") or []
        if isinstance(access_rights_raw, str):
            access_rights = [access_rights_raw]
        elif isinstance(access_rights_raw, list):
            access_rights = [str(item or "").strip() for item in access_rights_raw]
        else:
            access_rights = []
        if not any(right.lower() == "fullaccess" for right in access_rights):
            continue

        deny = record.get("Deny") if record.get("Deny") is not None else record.get("deny")
        if bool(deny):
            continue

        user_value = str(
            record.get("User") or record.get("user") or ""
        ).strip()
        if not user_value or user_value.lower() == _DIRECT_MAILBOX_PERMISSION_SELF:
            continue

        display_name, member_upn = _normalise_direct_mailbox_permission_principal(
            user_value
        )
        if not member_upn:
            continue

        members[member_upn] = {
            "member_display_name": display_name or member_upn,
            "member_upn": member_upn,
        }

    return sorted(members.values(), key=lambda item: item["member_display_name"].lower())


async def _exo_get_mailbox_permission(
    exo_token: str,
    tenant_id: str,
    mailbox_email: str,
) -> list[dict[str, Any]]:
    """Call Get-MailboxPermission for a single mailbox via Exchange Online REST API.

    Uses the Exchange Online PowerShell REST ``InvokeCommand`` endpoint to run
    ``Get-MailboxPermission -Identity <mailbox_email>``.  Returns the raw
    ``value`` list from the response, or an empty list on failure.
    """
    url = (
        f"https://outlook.office365.com/adminapi/beta/"
        f"{quote(tenant_id, safe='')}/InvokeCommand"
    )
    payload = {
        "CmdletInput": {
            "CmdletName": "Get-MailboxPermission",
            "Parameters": {"Identity": mailbox_email},
        }
    }
    headers = {
        "Authorization": f"Bearer {exo_token}",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json; charset=utf-8",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.DecodingError as exc:
        log_warning(
            "Exchange Online Get-MailboxPermission request decode failed",
            mailbox_email=mailbox_email,
            error=str(exc),
        )
        return []
    if response.status_code != 200:
        try:
            body = response.text[:500] if response.text else ""
        except httpx.DecodingError:
            body = "(decompression failed)"
        if response.status_code == 403:
            raise M365Error(
                f"Exchange Online Get-MailboxPermission returned 403 for "
                f"{mailbox_email}. Ensure the app has the Exchange.ManageAsApp "
                f"permission and an Exchange RBAC role (e.g. Exchange Administrator).",
                http_status=403,
            )
        log_warning(
            "Exchange Online Get-MailboxPermission failed",
            mailbox_email=mailbox_email,
            status=response.status_code,
            body=body,
        )
        return []
    try:
        data = response.json()
    except (ValueError, httpx.DecodingError) as exc:
        log_warning(
            "Exchange Online Get-MailboxPermission response parse failed",
            mailbox_email=mailbox_email,
            error=str(exc),
        )
        return []
    records = data.get("value") or []
    if records:
        log_info(
            "Exchange Online Get-MailboxPermission returned records",
            mailbox_email=mailbox_email,
            record_count=len(records),
        )
    return records


async def _fetch_exo_mailbox_permissions(
    company_id: int,
    mailbox_emails: set[str],
) -> dict[str, list[dict[str, str]]]:
    """Fetch FullAccess mailbox permissions via Exchange Online Get-MailboxPermission.

    Acquires an Exchange Online app-only token and calls
    ``Get-MailboxPermission`` for each mailbox via the Exchange Online
    PowerShell REST API.  This is the reliable method for retrieving direct
    FullAccess assignments, as Microsoft Graph does not expose this data.

    Returns a dict mapping lowercase mailbox emails to lists of member dicts
    (``member_display_name``, ``member_upn``).  The function is best-effort:
    if the Exchange Online token cannot be acquired or individual mailbox
    queries fail, those mailboxes are silently skipped.
    """
    if not mailbox_emails:
        return {}

    try:
        exo_token, effective_tenant_id = await _acquire_exo_access_token(company_id)
    except M365Error:
        return {}

    members_by_mailbox: dict[str, list[dict[str, str]]] = {}
    for mailbox_email in mailbox_emails:
        normalised = str(mailbox_email or "").strip().lower()
        if not normalised:
            continue
        try:
            records = await _exo_get_mailbox_permission(
                exo_token, effective_tenant_id, normalised
            )
        except M365Error as exc:
            if exc.http_status == 403:
                log_warning(
                    "Exchange Online Get-MailboxPermission returned 403 – "
                    "skipping remaining mailboxes. Ensure the app has the "
                    "Exchange.ManageAsApp permission and an Exchange RBAC role.",
                    mailbox_email=normalised,
                )
                break
            raise
        parsed = _parse_exo_mailbox_permission_records(normalised, records)
        if parsed:
            members_by_mailbox[normalised] = parsed

    return members_by_mailbox


async def sync_mailboxes(company_id: int) -> int:
    """Sync mailbox data for all users and shared mailboxes in the tenant.

    Uses the Microsoft Graph Reports API (``getMailboxUsageDetail``) to fetch
    primary and archive mailbox sizes for every mailbox in the tenant.  Enabled
    user accounts (from ``get_all_users``) are classified as ``UserMailbox``
    entries; all other active mailboxes in the report are classified as
    ``SharedMailbox`` entries (which typically covers shared mailboxes, room
    mailboxes and equipment mailboxes).

    For each user mailbox, inbox message rules are queried to count forwarding
    rules set up by the owner.  This requires the ``MailboxSettings.Read``
    application permission.  Forwarding rule counts default to 0 for shared
    mailboxes (and for user mailboxes where the rules endpoint is unavailable).

    For each enabled user, their mail-enabled M365 group memberships are fetched
    and stored in ``m365_mailbox_members`` (keyed by the group's primary SMTP
    address).  This builds a reverse index – for each group-backed mailbox, who
    are its members – so that ``get_mailbox_permissions()`` can report who can
    access a given mailbox without a live Graph round-trip.  Run a mailbox sync
    to refresh the data.

    Direct FullAccess mailbox permissions (assigned outside of group membership)
    are fetched via the Exchange Online PowerShell REST API using
    ``Get-MailboxPermission``.  This requires the ``Exchange.ManageAsApp``
    application permission and an appropriate Exchange RBAC role (e.g. Exchange
    Administrator) assigned to the provisioned service principal.  If Exchange
    Online access is unavailable, group-membership-based permissions are still
    synced.

    Requires the ``Reports.Read.All`` and ``MailboxSettings.Read`` application
    permissions granted to the provisioned enterprise app.  Re-provision the
    enterprise app to pick up these permissions if they were added after initial
    provisioning.

    :returns: The total number of mailboxes synced.
    """
    access_token = await acquire_access_token(company_id, force_client_credentials=True)

    try:
        report_items = await _fetch_mailbox_usage_report(access_token)
    except M365Error as exc:
        if exc.http_status == 403:
            raise M365Error(
                "Mailbox sync failed (403 Forbidden). The enterprise app is missing the "
                "Reports.Read.All permission. Re-provision the enterprise app to grant "
                "the required permissions. If you have just re-provisioned, please wait "
                "a few minutes for the permissions to take effect, then retry the sync."
            ) from exc
        raise

    def _looks_obfuscated_identifier(value: str) -> bool:
        """Return True for report identifiers that look privacy-obfuscated.

        Microsoft 365 usage reports can conceal mailbox/user identifiers using
        deterministic hashes when the tenant privacy option "Display concealed
        user, group, and site names in all reports" is enabled.  Those values
        are typically long hex strings without an ``@`` sign and cannot be
        reliably mapped back to real mailbox addresses.
        """
        candidate = str(value or "").strip().lower()
        if not candidate or "@" in candidate:
            return False
        if len(candidate) < 24:
            return False
        return bool(re.fullmatch(r"[0-9a-f]+", candidate))

    # Build a lookup: lower-case UPN -> report entry (skip deleted mailboxes).
    report_by_identifier: dict[str, dict[str, Any]] = {}
    report_primary_upns: set[str] = set()
    report_obfuscated_identifiers = 0
    for item in report_items:
        upn = (item.get("userPrincipalName") or "").lower().strip()
        if upn and not item.get("isDeleted"):
            report_by_identifier[upn] = item
            report_primary_upns.add(upn)
            if _looks_obfuscated_identifier(upn):
                report_obfuscated_identifiers += 1

    if report_primary_upns and report_obfuscated_identifiers >= max(
        1, int(len(report_primary_upns) * 0.8)
    ):
        raise M365Error(
            "Mailbox sync failed because Microsoft 365 reports are concealing mailbox identifiers. "
            "Disable the Microsoft 365 admin center privacy option 'Display concealed user, group, and "
            "site names in all reports', then run mailbox sync again."
        )

    def _user_identifiers(user: dict[str, Any]) -> list[str]:
        identifiers: list[str] = []
        for raw in (user.get("userPrincipalName"), user.get("mail")):
            value = str(raw or "").strip().lower()
            if value and value not in identifiers:
                identifiers.append(value)
        return identifiers

    # Get all users (enabled + disabled); mailboxes only exist for enabled accounts.
    users = await get_all_users(company_id)
    users_with_identifiers = [
        (u, _user_identifiers(u)) for u in users if u.get("accountEnabled", True)
    ]
    users_with_identifiers = [
        (user, identifiers)
        for user, identifiers in users_with_identifiers
        if identifiers
    ]

    rows_to_upsert: list[dict[str, Any]] = []
    matched_report_upns: set[str] = set()
    # Record the time before any member upserts so we can purge rows that
    # were not touched in this sync run using a simple timestamp comparison
    # (avoids building a large NOT IN clause for big tenants).
    # Truncate microseconds so the value stored in MySQL's DATETIME column
    # (which has only second precision) matches the value used in the stale
    # cleanup comparison.  Without this, MySQL may round the inserted value
    # down while comparing against the full-precision Python datetime,
    # causing freshly inserted rows to be deleted.
    member_sync_start = datetime.utcnow().replace(microsecond=0)

    # In-memory cache of group_email → list of (member_upn, member_display_name)
    # built alongside the user-centric sync so that we can cross-reference
    # shared mailbox UPNs with group members without extra API calls.
    group_member_cache: dict[str, list[tuple[str, str]]] = {}

    # Track whether the messageRules endpoint returned 403, which means the
    # MailboxSettings.Read permission is missing.  Once detected on the first
    # user, skip forwarding-rule checks for all remaining users to avoid
    # repeating N failing API calls (they would all fail identically).
    rules_permission_denied = False

    # --- User mailboxes ---
    for user, identifiers in users_with_identifiers:
        preferred_upn = identifiers[0]
        report_entry = next(
            (
                report_by_identifier.get(key)
                for key in identifiers
                if key in report_by_identifier
            ),
            {},
        )
        report_upn = str(report_entry.get("userPrincipalName") or "").strip().lower()
        if report_upn:
            matched_report_upns.add(report_upn)
        storage_bytes = int(report_entry.get("storageUsedInBytes") or 0)
        archive_raw = report_entry.get("archiveMailboxStorageUsedInBytes")
        archive_bytes = int(archive_raw) if archive_raw else 0
        # Use the dedicated "Has Archive" flag from the report when present;
        # fall back to inferring from bytes > 0 so that non-zero archive
        # storage is always captured even if the column is absent.
        has_archive = bool(report_entry.get("hasArchive")) or archive_bytes > 0
        display_name = (
            user.get("displayName") or report_entry.get("displayName") or preferred_upn
        )

        fw_count = 0
        if not rules_permission_denied:
            try:
                fw_count = await _count_forwarding_rules(access_token, user["id"])
            except M365Error as exc:
                if exc.http_status == 403:
                    rules_permission_denied = True
                    log_warning(
                        "Skipping forwarding-rule checks for all mailboxes – "
                        "the enterprise app is missing the MailboxSettings.Read "
                        "permission. Re-provision the enterprise app to grant "
                        "the required permissions.",
                    )
                else:
                    raise

        # Sync which mailbox groups this user has access to via group membership.
        # For each mail-enabled group the user belongs to, record a member row
        # so that get_mailbox_permissions() can show who can access a mailbox
        # without a live Graph round-trip.
        user_groups = await _get_user_mail_enabled_groups(access_token, user["id"])
        for group in user_groups:
            group_email = (group.get("mail") or "").strip().lower()
            if group_email:
                await m365_repo.upsert_mailbox_member(
                    company_id=company_id,
                    mailbox_email=group_email,
                    member_upn=preferred_upn,
                    member_display_name=user.get("displayName") or preferred_upn,
                    synced_at=member_sync_start,
                )
                group_member_cache.setdefault(group_email, []).append(
                    (preferred_upn, user.get("displayName") or preferred_upn)
                )

        rows_to_upsert.append(
            {
                "user_principal_name": preferred_upn,
                "display_name": display_name,
                "mailbox_type": "UserMailbox",
                "storage_used_bytes": storage_bytes,
                "archive_storage_used_bytes": archive_bytes if has_archive else None,
                "has_archive": has_archive,
                "forwarding_rule_count": fw_count,
            }
        )

    # --- Shared / non-user mailboxes ---
    for upn_lower in report_primary_upns:
        if upn_lower in matched_report_upns:
            continue  # already handled above
        entry = report_by_identifier.get(upn_lower, {})
        storage_bytes = int(entry.get("storageUsedInBytes") or 0)
        archive_raw = entry.get("archiveMailboxStorageUsedInBytes")
        archive_bytes = int(archive_raw) if archive_raw else 0
        has_archive = bool(entry.get("hasArchive")) or archive_bytes > 0
        display_name = entry.get("displayName") or upn_lower

        rows_to_upsert.append(
            {
                "user_principal_name": upn_lower,
                "display_name": display_name,
                "mailbox_type": "SharedMailbox",
                "storage_used_bytes": storage_bytes,
                "archive_storage_used_bytes": archive_bytes if has_archive else None,
                "has_archive": has_archive,
                "forwarding_rule_count": 0,
            }
        )

    # --- Cross-reference shared mailbox UPNs with group member cache ---
    # Group membership rows were stored above keyed by the group's primary SMTP
    # address (group_email).  When a shared mailbox UPN differs from the group
    # email (e.g. an onmicrosoft.com UPN vs. a custom-domain group address),
    # get_mailbox_permissions() needs to find those rows via proxy-address
    # resolution – which requires a live Graph API call that can fail.
    #
    # To make the data available under the shared mailbox UPN without depending
    # on a live call at query time, resolve each shared mailbox's email aliases
    # now and copy matching group-member entries to the mailbox UPN.
    if group_member_cache:
        for row in rows_to_upsert:
            if row["mailbox_type"] != "SharedMailbox":
                continue
            mb_upn = row["user_principal_name"]
            if mb_upn in group_member_cache:
                # Already stored under the correct key – nothing to do.
                continue

            # Resolve the shared mailbox's email aliases.
            try:
                mb_user = await _graph_get(
                    access_token,
                    (
                        f"https://graph.microsoft.com/v1.0/users/"
                        f"{quote(mb_upn, safe='')}"
                        "?$select=mail,proxyAddresses"
                    ),
                )
            except M365Error:
                mb_user = {}

            aliases: set[str] = set()
            mb_mail = (mb_user.get("mail") or "").strip().lower()
            if mb_mail and mb_mail != mb_upn:
                aliases.add(mb_mail)
            for proxy in mb_user.get("proxyAddresses") or []:
                proxy_str = str(proxy or "")
                if proxy_str.lower().startswith("smtp:"):
                    alias = proxy_str[5:].strip().lower()
                    if alias and alias != mb_upn:
                        aliases.add(alias)

            for alias in aliases:
                cached_members = group_member_cache.get(alias)
                if not cached_members:
                    continue
                for member_upn, member_display in cached_members:
                    await m365_repo.upsert_mailbox_member(
                        company_id=company_id,
                        mailbox_email=mb_upn,
                        member_upn=member_upn,
                        member_display_name=member_display,
                        synced_at=member_sync_start,
                    )

    mailbox_emails = {
        str(row["user_principal_name"] or "").strip().lower()
        for row in rows_to_upsert
        if str(row["user_principal_name"] or "").strip()
    }
    direct_members_by_mailbox: dict[str, list[dict[str, str]]] = {}
    if mailbox_emails:
        try:
            direct_members_by_mailbox = await _fetch_exo_mailbox_permissions(
                company_id, mailbox_emails
            )
        except Exception as exc:
            log_info(
                "Skipping direct mailbox permission sync; "
                "Exchange Online PowerShell unavailable",
                company_id=company_id,
                error=str(exc),
            )

    for mailbox_email, members in direct_members_by_mailbox.items():
        for member in members:
            await m365_repo.upsert_mailbox_member(
                company_id=company_id,
                mailbox_email=mailbox_email,
                member_upn=member["member_upn"],
                member_display_name=member["member_display_name"],
                synced_at=member_sync_start,
            )

    # Upsert all rows into the database.
    for row in rows_to_upsert:
        await m365_repo.upsert_mailbox(company_id=company_id, **row)

    # Remove stale entries (mailboxes that no longer exist in the tenant).
    current_upns = [r["user_principal_name"] for r in rows_to_upsert]
    await m365_repo.delete_stale_mailboxes(company_id, current_upns)

    # Purge mailbox-member rows that were not touched in this sync run.
    # Rows written above have synced_at == member_sync_start; older rows belong
    # to previous syncs and should be removed.
    await m365_repo.delete_stale_mailbox_members(company_id, member_sync_start)

    log_info(
        "M365 mailbox sync complete",
        company_id=company_id,
        total=len(rows_to_upsert),
        user_mailboxes=sum(
            1 for r in rows_to_upsert if r["mailbox_type"] == "UserMailbox"
        ),
        shared_mailboxes=sum(
            1 for r in rows_to_upsert if r["mailbox_type"] == "SharedMailbox"
        ),
    )
    return len(rows_to_upsert)


async def check_report_privacy(company_id: int) -> bool:
    """Check whether Microsoft 365 reports are concealing mailbox identifiers.

    Fetches the mailbox usage detail report and inspects the ``userPrincipalName``
    fields.  When the tenant-level privacy setting *Display concealed user, group,
    and site names in all reports* is enabled, Microsoft replaces real UPNs with
    deterministic hex hashes so they cannot be mapped back to real accounts.

    :returns: ``True`` if the report identifiers appear to be concealed, ``False``
        if they look like normal UPN / e-mail addresses.
    :raises M365Error: If the Graph API call fails (e.g. missing credentials or
        a 403 permission error).
    """
    access_token = await acquire_access_token(company_id, force_client_credentials=True)
    report_items = await _fetch_mailbox_usage_report(access_token)

    def _looks_obfuscated(value: str) -> bool:
        candidate = str(value or "").strip().lower()
        if not candidate or "@" in candidate:
            return False
        if len(candidate) < 24:
            return False
        return bool(re.fullmatch(r"[0-9a-f]+", candidate))

    primary_upns: set[str] = set()
    obfuscated_count = 0
    for item in report_items:
        upn = (item.get("userPrincipalName") or "").lower().strip()
        if upn and not item.get("isDeleted"):
            primary_upns.add(upn)
            if _looks_obfuscated(upn):
                obfuscated_count += 1

    if not primary_upns:
        return False
    return obfuscated_count >= max(1, int(len(primary_upns) * 0.8))


async def get_user_mailboxes(company_id: int) -> list[dict[str, Any]]:
    """Return stored user mailbox rows for the given company, excluding package mailboxes."""
    rows = await m365_repo.get_mailboxes(company_id, "UserMailbox")
    return [
        r for r in rows if not _PACKAGE_MAILBOX_RE.match(r.get("display_name") or "")
    ]


async def get_shared_mailboxes(company_id: int) -> list[dict[str, Any]]:
    """Return stored shared mailbox rows for the given company, excluding package mailboxes."""
    rows = await m365_repo.get_mailboxes(company_id, "SharedMailbox")
    return [
        r for r in rows if not _PACKAGE_MAILBOX_RE.match(r.get("display_name") or "")
    ]


async def get_mailbox_permissions(company_id: int, upn: str) -> dict[str, Any]:
    """Return mailbox permission details for a given UPN.

    Queries Microsoft Graph for **Mailboxes I can access** and combines
    pre-computed DB data with a live Exchange Online lookup for **Users
    that can access me**.

    **Mailboxes I can access** – mail-enabled M365 groups the given identity is
    a direct member of.  In Exchange Online, group membership on a group that
    backs a shared mailbox grants FullAccess.

    **Users that can access me** – users who have been assigned full access
    to this mailbox.  Data is gathered from three sources:

    1. Pre-synced ``m365_mailbox_members`` rows written by ``sync_mailboxes``
       (group memberships and previous Exchange Online sync results).
    2. A live lookup of the M365 group backing the mailbox (if any).
    3. A live Exchange Online ``Get-MailboxPermission`` call that returns
       direct FullAccess assignments.  This ensures results appear even
       when a mailbox sync has not run or Exchange Online was unavailable
       during the last sync.

    Requires the ``Directory.Read.All`` application permission (already in
    ``_PROVISION_APP_ROLES``).

    :returns: A dict with keys ``can_access`` (list of dicts with
        ``display_name`` and ``email``) and ``accessible_by`` (list of dicts
        with ``display_name`` and ``upn``).
    """
    access_token = await acquire_access_token(company_id, force_client_credentials=True)

    raw_mailbox_email = upn.lower().strip()
    accessible_by_map: dict[str, dict[str, Any]] = {}

    def _store_accessible_member(display_name: str | None, member_upn: str | None) -> None:
        normalised_upn = str(member_upn or "").strip().lower()
        if not normalised_upn:
            return
        accessible_by_map[normalised_upn] = {
            "display_name": str(display_name or "").strip() or normalised_upn,
            "upn": normalised_upn,
        }

    def _store_accessible_members(members: list[dict[str, Any]]) -> None:
        for member in members:
            _store_accessible_member(
                member.get("member_display_name"),
                member.get("member_upn"),
            )

    def _store_group_members(members: list[dict[str, Any]]) -> None:
        for member in members:
            _store_accessible_member(
                member.get("displayName") or member.get("mail"),
                member.get("userPrincipalName") or member.get("mail"),
            )

    # Start with the mailbox identifier requested by the UI so shared mailboxes
    # can still show synced access data even when they are not resolvable via
    # the /users Graph endpoint.
    _store_accessible_members(
        await m365_repo.get_mailbox_members(company_id, raw_mailbox_email)
    )

    # Look up the user/mailbox directory object to get its stable ID and, when
    # available, its primary SMTP address. The primary SMTP address (mail) is
    # used for a second DB member lookup because some tenants store a different
    # UPN in Exchange usage reports than the M365 group's primary email (e.g.
    # an onmicrosoft.com UPN vs a custom-domain group email address).
    encoded_upn = quote(upn, safe="")
    try:
        user_data = await _graph_get(
            access_token,
            f"https://graph.microsoft.com/v1.0/users/{encoded_upn}?$select=id,displayName,mail,proxyAddresses",
        )
    except M365Error:
        user_data = {}

    mailbox_email = (user_data.get("mail") or raw_mailbox_email).lower().strip()

    # Build a de-duplicated, ordered collection of every known email alias for
    # this mailbox.  proxyAddresses include all SMTP aliases (primary +
    # secondary) so the DB and live lookups can match even when the group email
    # used during sync differs from the UPN shown in usage reports.
    all_emails: dict[str, None] = dict.fromkeys([raw_mailbox_email, mailbox_email])
    for proxy in user_data.get("proxyAddresses") or []:
        proxy_str = str(proxy or "")
        if proxy_str.lower().startswith("smtp:"):
            alias = proxy_str[5:].strip().lower()
            if alias:
                all_emails[alias] = None

    for candidate_email in all_emails:
        if candidate_email == raw_mailbox_email:
            continue  # initial DB lookup above already covered this email
        _store_accessible_members(
            await m365_repo.get_mailbox_members(company_id, candidate_email)
        )

    # Supplement cached data with a live lookup of the mailbox's backing M365
    # group so mailbox-centric views stay accurate even when a mailbox sync has
    # not run since the latest permission change.
    for candidate_email in all_emails:
        if not candidate_email:
            continue
        _store_group_members(
            await _get_mailbox_group_members(access_token, candidate_email)
        )

    # Supplement with a live Exchange Online Get-MailboxPermission lookup so
    # that direct FullAccess assignments appear even when a mailbox sync has
    # not run or the sync's Exchange Online step was unavailable.
    try:
        exo_token, effective_tenant_id = await _acquire_exo_access_token(company_id)
        records = await _exo_get_mailbox_permission(
            exo_token, effective_tenant_id, raw_mailbox_email
        )
        for member in _parse_exo_mailbox_permission_records(raw_mailbox_email, records):
            _store_accessible_member(member["member_display_name"], member["member_upn"])
    except Exception as exc:
        log_warning(
            "Live Exchange Online mailbox permission lookup failed",
            mailbox_email=raw_mailbox_email,
            error=str(exc),
        )

    # Fallback: if all previous sources yielded nothing and the Graph API
    # user lookup failed (so proxy-address resolution was unavailable), try a
    # local-part-based DB search.  This catches the common case where group
    # membership data was synced under a different domain variant of the same
    # mailbox (e.g. group email is sales@contoso.com but the report UPN is
    # sales@contoso.onmicrosoft.com).
    if not accessible_by_map and "@" in raw_mailbox_email:
        local_part = raw_mailbox_email.split("@", 1)[0]
        if local_part:
            _store_accessible_members(
                await m365_repo.get_mailbox_members_by_local_part(
                    company_id, local_part
                )
            )

    # ------------------------------------------------------------------
    # "Mailboxes I can access": live mail-enabled group memberships
    # ------------------------------------------------------------------
    user_id = user_data.get("id")
    groups: list[dict[str, Any]] = []
    if user_id:
        member_of_url = (
            f"https://graph.microsoft.com/v1.0/users/{user_id}/memberOf"
            "?$select=id,displayName,mail,mailEnabled"
        )
        try:
            groups = await _graph_get_all(access_token, member_of_url)
        except M365Error:
            groups = []

    can_access: list[dict[str, Any]] = []
    for group in groups:
        if group.get("mailEnabled") and group.get("mail"):
            can_access.append(
                {
                    "display_name": group.get("displayName") or group["mail"],
                    "email": group["mail"],
                }
            )
    can_access.sort(key=lambda x: x["display_name"].lower())

    # ------------------------------------------------------------------
    # "Users that can access me": from synced data, live group members,
    # and live Exchange Online Get-MailboxPermission results
    # ------------------------------------------------------------------
    accessible_by = list(accessible_by_map.values())
    accessible_by.sort(key=lambda x: x["display_name"].lower())

    return {"can_access": can_access, "accessible_by": accessible_by}
