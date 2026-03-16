from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import apps as apps_repo
from app.repositories import companies as companies_repo
from app.repositories import licenses as license_repo
from app.repositories import integration_modules as modules_repo
from app.repositories import m365 as m365_repo
from app.repositories import staff as staff_repo
from app.security.encryption import decrypt_secret, encrypt_secret


_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

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
]

# OAuth scopes requested during the admin-consent provisioning flow
PROVISION_SCOPE = (
    "https://graph.microsoft.com/Application.ReadWrite.All "
    "https://graph.microsoft.com/AppRoleAssignment.ReadWrite.All offline_access"
)

# Minimal scopes used for the tenant-discovery sign-in step
DISCOVER_SCOPE = "openid profile"

# Scopes for CSP/Lighthouse GDAP sign-in (needs Directory.Read.All for /contracts)
CSP_SCOPE = "https://graph.microsoft.com/Directory.Read.All openid profile offline_access"

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
    """Raised when Microsoft 365 operations fail."""


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE ``code_verifier`` / ``code_challenge`` pair.

    Returns a tuple of ``(code_verifier, code_challenge)`` where the challenge
    is the URL-safe base64-encoded SHA-256 hash of the verifier (S256 method).
    The verifier is a 32-byte cryptographically random string.
    """
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
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
) -> tuple[str, str | None, datetime | None]:
    token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data: dict[str, Any]
    if refresh_token:
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": f"{_GRAPH_SCOPE} offline_access",
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    else:
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": _GRAPH_SCOPE,
            "grant_type": "client_credentials",
        }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(token_endpoint, data=data)
    if response.status_code != 200:
        log_error(
            "Failed to acquire Microsoft 365 token",
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
        expires_at = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(seconds=float(expires_in))
    return access_token, str(new_refresh) if new_refresh else None, expires_at


async def acquire_access_token(company_id: int) -> str:
    creds = await get_credentials(company_id)
    if not creds:
        raise M365Error("Microsoft 365 credentials have not been configured")

    # Reuse a stored token that is still valid (with a 5-minute safety margin).
    # This avoids an unnecessary round-trip to Microsoft's token endpoint on
    # every call (e.g. after an app restart) and prevents transient failures
    # from breaking sync jobs when a perfectly valid token is already cached.
    stored_token = creds.get("access_token")
    stored_expires_at = creds.get("token_expires_at")
    if stored_token and stored_expires_at:
        # token_expires_at is stored as a naive UTC datetime; compare likewise.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        margin = timedelta(minutes=5)
        if isinstance(stored_expires_at, datetime) and stored_expires_at - margin > now_utc:
            return stored_token

    # Prefer the company's mapped CSP tenant ID when available.  This ensures
    # that a shared CSP admin app (registered in the partner/parent tenant) still
    # acquires a token scoped to the *customer* tenant rather than the parent,
    # which would otherwise cause /subscribedSkus to return the parent's licenses.
    csp_tenant_id = await companies_repo.get_company_csp_tenant_id(company_id)
    effective_tenant_id = csp_tenant_id or creds["tenant_id"]
    stored_refresh = creds.get("refresh_token")
    try:
        access_token, refresh, expires_at = await _exchange_token(
            tenant_id=effective_tenant_id,
            client_id=creds["client_id"],
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
        )
        access_token, refresh, expires_at = await _exchange_token(
            tenant_id=effective_tenant_id,
            client_id=creds["client_id"],
            client_secret=creds.get("client_secret") or "",
            refresh_token=None,
        )
        # Clear the stale refresh token so future calls use client_credentials
        # immediately rather than attempting the refresh_token grant again.
        refresh = None
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
        log_error("Microsoft Graph request failed", url=url, status=response.status_code, body=response.text)
        raise M365Error(f"Microsoft Graph request failed ({response.status_code})")
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
        raise M365Error(f"Microsoft Graph POST failed ({response.status_code})")
    if response.status_code == 204:
        return {}
    return response.json()


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

    # 1. Create the app registration
    app_payload: dict[str, Any] = {
        "displayName": display_name,
        "signInAudience": "AzureADMyOrg",
        "requiredResourceAccess": [
            {
                "resourceAppId": _GRAPH_APP_ID,
                "resourceAccess": [
                    {"id": role_id, "type": "Role"}
                    for role_id in _PROVISION_APP_ROLES
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

    # 4. Grant admin consent for each required application permission
    for role_id in _PROVISION_APP_ROLES:
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
        "Granted admin consent for provisioned M365 app",
        sp_object_id=sp_object_id,
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
                    *[{"id": role_id, "type": "Role"} for role_id in _CSP_ADMIN_APP_ROLES],
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
        "client_secret_key_id": str(settings.get("client_secret_key_id") or "").strip() or None,
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
    log_info("Updated M365 admin credentials in integration module", client_id=client_id)


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
    url: str | None = (
        "https://graph.microsoft.com/v1.0/users?"
        f"$filter=assignedLicenses/any(x:x/skuId eq {sku_id})&"
        "$select=id,displayName,mail,userPrincipalName,givenName,surname&"
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
            email = (user.get("mail") or user.get("userPrincipalName") or "").strip().lower()
            if not email:
                continue
            staff = await staff_repo.get_staff_by_company_and_email(company_id, email)
            if not staff:
                first = (user.get("givenName") or "").strip() or "Unknown"
                last = (user.get("surname") or "").strip() or user.get("displayName") or ""
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
                )
                staff = created
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
    access_token = await acquire_access_token(company_id)
    payload = await _graph_get(access_token, "https://graph.microsoft.com/v1.0/subscribedSkus")
    for sku in payload.get("value", []):
        part_number = str(sku.get("skuPartNumber") or "").strip()
        sku_id = sku.get("skuId")
        prepaid = sku.get("prepaidUnits", {})
        count = int(prepaid.get("enabled") or 0)
        app = None
        if part_number:
            app = await apps_repo.get_app_by_vendor_sku(part_number)
        name = app.get("name") if app else part_number or "Unknown SKU"
        existing = await license_repo.get_license_by_company_and_sku(company_id, part_number)
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
        if sku_id:
            await _sync_staff_assignments(
                company_id=company_id,
                license_id=int(license_id),
                access_token=access_token,
                sku_id=str(sku_id),
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
    """Return all enabled M365 users for the given company.

    Fetches members from the Microsoft Graph ``/users`` endpoint and handles
    ``@odata.nextLink`` pagination so that tenants with more than the default
    page size are fully returned.

    ``$filter=accountEnabled eq true`` is an advanced directory-object query
    that requires the ``ConsistencyLevel: eventual`` request header and the
    ``$count=true`` query parameter.  Without these, Microsoft Graph returns
    403 Forbidden in many tenant configurations (application-permission context).
    The same header must also be forwarded for every ``@odata.nextLink``
    paginated request, otherwise subsequent pages will also fail.
    """
    access_token = await acquire_access_token(company_id)
    # $count=true is required alongside ConsistencyLevel: eventual for advanced
    # filter queries on directory objects.
    url = (
        "https://graph.microsoft.com/v1.0/users?"
        "$select=id,displayName,mail,userPrincipalName,givenName,surname,"
        "mobilePhone,businessPhones,streetAddress,city,state,postalCode,country,"
        "department,jobTitle&"
        "$filter=accountEnabled eq true&"
        "$count=true"
    )
    consistency_headers = {"ConsistencyLevel": "eventual"}
    users: list[dict[str, Any]] = []
    while url:
        payload = await _graph_get(access_token, url, extra_headers=consistency_headers)
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
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=float(expires_in))
        )
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
        str(a.get("appRoleId") or "")
        for a in assignments_response.get("value", [])
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


async def ensure_service_principal_for_app(access_token: str, app_id: str) -> dict[str, Any]:
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

