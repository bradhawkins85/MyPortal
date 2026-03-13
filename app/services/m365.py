from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.logging import log_error, log_info
from app.repositories import apps as apps_repo
from app.repositories import licenses as license_repo
from app.repositories import m365 as m365_repo
from app.repositories import staff as staff_repo
from app.security.encryption import decrypt_secret, encrypt_secret


_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Microsoft Graph's own well-known app ID (constant across all tenants)
_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# Application-permission role IDs required for the provisioned integration app
_PROVISION_APP_ROLES: list[str] = [
    "df021288-bdef-4463-88db-98f22de89214",  # User.Read.All
    "7ab1d382-f21e-4acd-a863-ba3e13f7da61",  # Directory.Read.All
]

# OAuth scopes requested during the admin-consent provisioning flow
PROVISION_SCOPE = (
    "Application.ReadWrite.All AppRoleAssignment.ReadWrite.All offline_access"
)

# Minimal scopes used for the tenant-discovery sign-in step
DISCOVER_SCOPE = "openid profile"


class M365Error(RuntimeError):
    """Raised when Microsoft 365 operations fail."""


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
) -> dict[str, Any]:
    await m365_repo.upsert_credentials(
        company_id=company_id,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=_encrypt(client_secret),
        refresh_token=None,
        access_token=None,
        token_expires_at=None,
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
    access_token, refresh, expires_at = await _exchange_token(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds.get("client_secret") or "",
        refresh_token=creds.get("refresh_token"),
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


async def _graph_get(access_token: str, url: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
    if response.status_code != 200:
        log_error("Microsoft Graph request failed", url=url, status=response.status_code, body=response.text)
        raise M365Error("Microsoft Graph request failed")
    return response.json()


async def _graph_post(
    access_token: str,
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code not in (200, 201):
        log_error(
            "Microsoft Graph POST failed",
            url=url,
            status=response.status_code,
            body=response.text,
        )
        raise M365Error(f"Microsoft Graph POST failed ({response.status_code})")
    return response.json()


async def provision_app_registration(
    *,
    access_token: str,
    display_name: str = "MyPortal Integration",
) -> tuple[str, str]:
    """Create a per-tenant app registration with required permissions.

    Uses a delegated *access_token* obtained via the admin-consent OAuth flow to:
    1. Create an App Registration in the tenant.
    2. Create the corresponding Service Principal (Enterprise App).
    3. Find the Microsoft Graph service principal in the tenant.
    4. Grant admin consent for each required application permission.
    5. Generate and return a client secret for the new app.

    Returns a ``(client_id, client_secret)`` tuple.  The client secret is
    returned in plain text exactly once and must be stored immediately.
    """
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

    # 5. Create a client secret; valid for approximately two years (730 days)
    secret_expiry = (
        (date.today() + timedelta(days=730)).isoformat() + "T00:00:00Z"
    )
    secret_data = await _graph_post(
        access_token,
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}/addPassword",
        {
            "passwordCredential": {
                "displayName": "MyPortal",
                "endDateTime": secret_expiry,
            }
        },
    )
    client_secret: str = secret_data["secretText"]
    log_info(
        "Created client secret for provisioned M365 app",
        client_id=client_id,
    )

    return client_id, client_secret


async def _sync_staff_assignments(
    *,
    company_id: int,
    license_id: int,
    access_token: str,
    sku_id: str,
) -> None:
    url = (
        "https://graph.microsoft.com/v1.0/users?"
        f"$filter=assignedLicenses/any(x:x/skuId eq {sku_id})&"
        "$select=id,displayName,mail,userPrincipalName,givenName,surname"
    )
    payload = await _graph_get(access_token, url)
    assigned_emails: set[str] = set()
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

