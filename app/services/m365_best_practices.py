"""Microsoft 365 Best Practices service.

Implements a curated set of Microsoft 365 best-practice checks for tenant
configurations.  Modelled after the CIS Benchmark service (see
``app/services/cis_benchmark.py``) but with two key differences:

* Best Practices are enabled/disabled **globally** by super administrators
  (a single switch per check applied across all companies), rather than per
  company exclusions.
* Each company gets its own set of evaluation results stored in the
  ``m365_best_practice_results`` table.

Several of the underlying Graph queries reuse helper functions from
``cis_benchmark`` to avoid duplication.  Each best-practice check has its
own ``bp_*`` identifier and its own user-facing name and remediation text
suitable for the Best Practices page.

Some checks (e.g. ``bp_disable_direct_send``) query Exchange Online via the
REST InvokeCommand API instead of Microsoft Graph; these are marked with
``"source_type": "exo"`` in the catalog and their runner callables accept
``(exo_token, tenant_id)`` rather than a single Graph token string.

CIS Benchmark checks (from the CIS Microsoft 365 Foundations Benchmark and
CIS Microsoft Intune Benchmarks) are merged into this catalog.  Catalog
entries sourced from a CIS Benchmark are flagged with ``"is_cis_benchmark": True``.
Intune checks are grouped under a ``"cis_group"`` key
(``"intune_windows"``, ``"intune_ios"``, or ``"intune_macos"``) and run via
the batch runners in ``cis_benchmark.py``.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Awaitable, Callable, Mapping, Union

import httpx

from app.core.logging import log_error, log_info
from app.core.config import get_settings
from app.repositories import companies as companies_repo
from app.repositories import m365_best_practices as bp_repo
from app.repositories import tickets as tickets_repo
from app.services import hudu as hudu_service
from app.services import tickets as tickets_service
from app.services.cis_benchmark import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    STATUS_NOT_APPLICABLE,
    _fail,
    _check_admin_mfa,
    _check_audit_log_enabled,
    _check_global_admin_count,
    _check_guest_access_restricted,
    _check_legacy_auth_blocked,
    _check_mfa_conditional_access,
    _check_monitor_ca_report_only_policies,
    _check_monitor_cloud_admin_accounts,
    _check_monitor_mfa_registration_policy,
    _check_monitor_named_locations,
    _check_monitor_risky_users,
    _check_monitor_secure_score,
    _check_monitor_sign_in_logs,
    _check_monitor_sign_in_risk_policy,
    _check_monitor_user_risk_policy,
    _check_password_never_expires,
    _check_security_defaults,
    _check_sspr_enabled,
    _pass,
    _unknown,
    run_intune_ios_benchmarks,
    run_intune_macos_benchmarks,
    run_intune_windows_benchmarks,
)
from app.services.m365 import (
    M365Error,
    _acquire_exo_access_token,
    _acquire_scc_access_token,
    _coerce_exo_bool,
    _exo_invoke_command,
    _graph_delete,
    _graph_get,
    _graph_get_all,
    _graph_patch,
    _graph_post,
    _parse_client_secret_expires,
    _post_app_role_assignment_with_retry,
    _scc_invoke_command,
    acquire_access_token,
    acquire_delegated_token,
    get_admin_m365_credentials,
    get_company_admin_credentials,
    get_effective_admin_credentials,
    renew_admin_client_secret,
    try_grant_missing_permissions,
)


# ---------------------------------------------------------------------------
# Tenant capability detection (license-based)
# ---------------------------------------------------------------------------
#
# Several Best Practice checks can only be implemented when the tenant has
# specific Microsoft 365 licenses.  For example, blocking legacy
# authentication requires a Conditional Access policy (Microsoft Entra ID P1
# or higher), and Identity-Protection-based checks (risky users, sign-in /
# user risk policies) require Microsoft Entra ID P2.  When the tenant does
# not have the required licenses, these checks cannot meaningfully be
# evaluated or remediated, and we mark them as ``not_applicable`` so that
# administrators are not asked to remediate something they cannot implement.
#
# Capabilities are derived from the ``subscribedSkus`` Graph endpoint by
# inspecting each SKU's ``servicePlans`` collection.  A SKU "grants" a
# capability when:
#   * the subscription has at least one prepaid unit enabled, AND
#   * the corresponding service plan is provisioned and not disabled
#     (``provisioningStatus`` not equal to ``"Disabled"``).
#
# The mapping below translates well-known Microsoft service-plan GUIDs into
# capability identifiers used by ``requires_licenses`` on catalog entries.
# Service plan IDs are stable identifiers published by Microsoft – see
# https://learn.microsoft.com/en-us/entra/identity/users/licensing-service-plan-reference

# Capability identifiers
CAP_ENTRA_ID_P1 = "entra_id_p1"
CAP_ENTRA_ID_P2 = "entra_id_p2"
CAP_INTUNE = "intune"
CAP_EXCHANGE_ONLINE = "exchange_online"
CAP_EXCHANGE_ONLINE_P2 = "exchange_online_p2"
CAP_SHAREPOINT_ONLINE = "sharepoint_online"
CAP_TEAMS = "teams"
CAP_TEAMS_AUDIO_CONF = "teams_audio_conferencing"
CAP_DEFENDER_O365_P1 = "defender_o365_p1"
CAP_DEFENDER_O365_P2 = "defender_o365_p2"
CAP_PURVIEW_DLP = "purview_dlp"
CAP_INTUNE_LAPS = "intune_laps"

_M365_FAILURE_TICKET_CATEGORY = "Microsoft 365"
_M365_FAILURE_TICKET_MODULE = "m365_admin"

# Friendly names used in the "not applicable" details message
_CAPABILITY_FRIENDLY_NAMES: dict[str, str] = {
    CAP_ENTRA_ID_P1: "Microsoft Entra ID P1",
    CAP_ENTRA_ID_P2: "Microsoft Entra ID P2",
    CAP_INTUNE: "Microsoft Intune",
    CAP_EXCHANGE_ONLINE: "Exchange Online",
    CAP_EXCHANGE_ONLINE_P2: "Exchange Online Plan 2",
    CAP_SHAREPOINT_ONLINE: "SharePoint Online",
    CAP_TEAMS: "Microsoft Teams",
    CAP_TEAMS_AUDIO_CONF: "Microsoft Teams Audio Conferencing",
    CAP_DEFENDER_O365_P1: "Microsoft Defender for Office 365 P1",
    CAP_DEFENDER_O365_P2: "Microsoft Defender for Office 365 P2",
    CAP_PURVIEW_DLP: "Microsoft Purview DLP (Information Protection & Governance)",
    CAP_INTUNE_LAPS: "Microsoft Intune (with Windows LAPS support)",
}

# Service plan GUIDs (lower-case) that grant each capability.  Entra ID P2
# always includes Entra ID P1 features.  Service plan IDs are stable
# identifiers published by Microsoft – see
# https://learn.microsoft.com/en-us/entra/identity/users/licensing-service-plan-reference
_SERVICE_PLAN_TO_CAPABILITIES: dict[str, set[str]] = {
    # AAD_PREMIUM (Entra ID P1)
    "41781fb2-bc02-4b7c-bd55-b576c07bb09d": {CAP_ENTRA_ID_P1},
    # AAD_PREMIUM_P2 (Entra ID P2 – includes P1)
    "eec0eb4f-6444-4f95-aba0-50c24d67f998": {CAP_ENTRA_ID_P1, CAP_ENTRA_ID_P2},
    # INTUNE_A (Microsoft Intune) – also grants the LAPS capability
    "c1ec4a95-1f05-45b3-a911-aa3fa01094f5": {CAP_INTUNE, CAP_INTUNE_LAPS},
    # EXCHANGE_S_STANDARD (Exchange Online Plan 1)
    "9aaf7827-d63c-4b61-89c3-182f06f82e5c": {CAP_EXCHANGE_ONLINE},
    # EXCHANGE_S_ENTERPRISE (Exchange Online Plan 2 – includes Plan 1 features)
    "efb87545-963c-4e0d-99df-69c6916d9eb0": {CAP_EXCHANGE_ONLINE, CAP_EXCHANGE_ONLINE_P2},
    # EXCHANGE_S_FOUNDATION (bundled in many plans – also enables EXO)
    "113feb6c-3fe4-4440-bddc-54d774bf0318": {CAP_EXCHANGE_ONLINE},
    # SHAREPOINTSTANDARD (SharePoint Online Plan 1)
    "c7699d2e-19aa-44de-8edf-1736da088ca1": {CAP_SHAREPOINT_ONLINE},
    # SHAREPOINTENTERPRISE (SharePoint Online Plan 2)
    "5dbe027f-2339-4123-9542-606e4d348a72": {CAP_SHAREPOINT_ONLINE},
    # TEAMS1 (Microsoft Teams)
    "57ff2da0-773e-42df-b2af-ffb7a2317929": {CAP_TEAMS},
    # MCOMEETADV (Audio Conferencing)
    "3e26ee1f-8a5f-4d52-aee2-b81ce45c8f40": {CAP_TEAMS_AUDIO_CONF},
    # ATP_ENTERPRISE (Microsoft Defender for Office 365 P1)
    "f20fedf3-f3c3-43c3-8267-2bfdd51c0939": {CAP_DEFENDER_O365_P1},
    # THREAT_INTELLIGENCE (Microsoft Defender for Office 365 P2 – includes P1)
    "8e0c0a52-6a6c-4d40-8370-dd62790dcd70": {CAP_DEFENDER_O365_P1, CAP_DEFENDER_O365_P2},
    # INFORMATION_PROTECTION_AND_GOVERNANCE_STANDARD (Purview – DLP-capable)
    "8f0c0a52-6a6c-4d40-8370-dd62790dcd71": {CAP_PURVIEW_DLP},
    # MIP_S_CLP1 (Information Protection for O365 – Standard)
    "5136a095-5cf0-4aff-bec3-e84448b38ea5": {CAP_PURVIEW_DLP},
    # INFORMATION_BARRIERS / E5 compliance plan (also DLP-capable)
    "c4801e8a-cb58-4c35-aca6-f2dcc106f287": {CAP_PURVIEW_DLP},
}


def _detect_capabilities_from_skus(skus_payload: dict[str, Any]) -> set[str]:
    """Return the set of capabilities granted by the tenant's subscribed SKUs.

    ``skus_payload`` is the raw response from
    ``GET https://graph.microsoft.com/v1.0/subscribedSkus``.
    """
    capabilities: set[str] = set()
    for sku in skus_payload.get("value", []) or []:
        prepaid = sku.get("prepaidUnits") or {}
        try:
            enabled_units = int(prepaid.get("enabled") or 0)
        except (TypeError, ValueError):
            enabled_units = 0
        if enabled_units <= 0:
            continue
        for plan in sku.get("servicePlans") or []:
            plan_id = str(plan.get("servicePlanId") or "").strip().lower()
            if not plan_id:
                continue
            provisioning = str(plan.get("provisioningStatus") or "").strip().lower()
            if provisioning == "disabled":
                continue
            granted = _SERVICE_PLAN_TO_CAPABILITIES.get(plan_id)
            if granted:
                capabilities |= granted
    return capabilities


async def detect_tenant_capabilities(graph_token: str) -> set[str] | None:
    """Detect the licensing-derived capabilities of the tenant.

    Returns ``None`` (capabilities unknown – fall back to running every
    enabled check normally) if the call fails for any reason.  This keeps
    the Best Practices runner robust when the tenant does not grant the
    Directory.Read.All / Organization.Read.All permissions required by
    ``subscribedSkus``.
    """
    try:
        payload = await _graph_get(
            graph_token, "https://graph.microsoft.com/v1.0/subscribedSkus"
        )
    except Exception as exc:  # noqa: BLE001 – capability detection must never break the runner
        log_info(
            "M365 best practices: tenant capability detection skipped",
            error=str(exc),
        )
        return None
    return _detect_capabilities_from_skus(payload)


def _missing_capabilities(
    required: list[str] | None, capabilities: set[str] | None
) -> list[str]:
    """Return the subset of ``required`` capabilities the tenant lacks.

    ``capabilities`` of ``None`` (detection failed/skipped) means we cannot
    determine missing licenses, so an empty list is returned (do not mark
    the check as N/A).
    """
    if not required or capabilities is None:
        return []
    return [cap for cap in required if cap not in capabilities]


def _format_missing_licenses(missing: list[str]) -> str:
    return ", ".join(_CAPABILITY_FRIENDLY_NAMES.get(cap, cap) for cap in missing)


# ---------------------------------------------------------------------------
# Best Practice catalog
# ---------------------------------------------------------------------------
#
# Each entry describes a Microsoft 365 best-practice check.  The ``source``
# callable is an existing CIS-benchmark Graph helper that produces a result
# dict with its own ``check_id``/``check_name``; we re-key the result to use
# the ``bp_*`` id and Best-Practices-specific name when persisting.
#
# Entries with ``"source_type": "exo"`` call an Exchange Online InvokeCommand
# runner that takes ``(exo_token: str, tenant_id: str)`` instead of a single
# Graph token string.
#
# Entries with ``"has_remediation": True`` support one-click automated
# remediation via the ``remediate_check`` service function.

# Runner types
GraphRunner = Callable[[str], Awaitable[dict[str, Any]]]
ExoRunner = Callable[[str, str], Awaitable[dict[str, Any]]]
BestPracticeRunner = Union[GraphRunner, ExoRunner]

# Keys that are implementation details and must not be exposed in the public catalog
_INTERNAL_KEYS = frozenset({"source", "source_type", "remediation_cmdlet", "remediation_params", "remediation_url", "remediation_payload", "remediation_type", "remediation_mailbox_params", "default_auto_remediate", "uses_company_id"})

_GLOBAL_ADMIN_ROLE_DEFINITION_ID = "62e90394-69f5-4237-9190-012177145e10"

def _generate_emergency_admin_password(length: int = 32) -> str:
    """Generate a CSPRNG-backed password satisfying Entra complexity rules."""
    chars = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase),
             secrets.choice(string.digits), secrets.choice("!@#$%^&*-_=+")]
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


async def _resolve_myportal_pkce_credential_target(company_id: int) -> dict[str, Any] | None:
    """Return the credential record MyPortal currently uses for PKCE/bootstrap flows."""
    company_creds = await get_company_admin_credentials(company_id)
    if company_creds and company_creds.get("client_id") and company_creds.get("client_secret"):
        return {"scope": "company", "credentials": company_creds}

    global_creds = await get_admin_m365_credentials()
    if global_creds and global_creds.get("client_id") and global_creds.get("client_secret"):
        return {"scope": "global", "credentials": global_creds}

    effective = await get_effective_admin_credentials(company_id)
    if effective and effective.get("client_id") and effective.get("client_secret"):
        return {"scope": "environment", "credentials": effective}
    return None


async def _check_myportal_pkce_app_credential_expiry(
    token: str, company_id: int
) -> dict[str, Any]:
    check_id = "bp_monitor_app_credential_expiry"
    check_name = "No app registration credentials expiring within 30 days"
    target = await _resolve_myportal_pkce_credential_target(company_id)
    if not target:
        return _unknown(
            check_id,
            check_name,
            "MyPortal PKCE/bootstrap admin credentials are not configured.",
        )

    creds = target["credentials"]
    client_id = str(creds.get("client_id") or "").strip()
    if not client_id:
        return _unknown(
            check_id,
            check_name,
            "MyPortal PKCE/bootstrap app ID is not configured.",
        )

    app_id_filter = client_id.replace("'", "''")
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/applications"
            f"?$filter=appId eq '{app_id_filter}'"
            "&$select=id,appId,displayName,passwordCredentials,keyCredentials",
        )
    except M365Error as exc:
        return _unknown(
            check_id,
            check_name,
            f"Unable to retrieve the configured MyPortal PKCE app registration: {exc}",
        )

    app = next(iter(data.get("value") or []), None)
    if not app:
        return _unknown(
            check_id,
            check_name,
            f"The configured MyPortal PKCE app registration ({client_id}) was not found.",
        )

    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=30)
    expiring: list[tuple[str, datetime]] = []
    for cred_type, credentials in (
        ("secret", app.get("passwordCredentials") or []),
        ("certificate", app.get("keyCredentials") or []),
    ):
        for cred in credentials:
            end_raw = cred.get("endDateTime")
            if not end_raw:
                continue
            try:
                end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if now <= end_dt <= threshold:
                expiring.append((cred_type, end_dt))

    app_name = str(app.get("displayName") or client_id)
    if not expiring:
        return _pass(
            check_id,
            check_name,
            f"MyPortal PKCE app '{app_name}' ({client_id}) has no active credentials expiring within 30 days.",
        )

    earliest_type, earliest_expiry = min(expiring, key=lambda item: item[1])
    active_expiry = _parse_client_secret_expires(creds.get("client_secret_expires_at"))
    active_hint = (
        f" Stored active credential expiry: {active_expiry.date().isoformat()}."
        if active_expiry
        else ""
    )
    return _fail(
        check_id,
        check_name,
        f"MyPortal PKCE app '{app_name}' ({client_id}) has an expiring {earliest_type} "
        f"credential on {earliest_expiry.date().isoformat()}.{active_hint}",
    )

async def _remediate_global_admin_count(graph_token: str, company_id: int) -> tuple[bool, str]:
    """Create and document enough emergency administrators to reach the target of three."""
    company = await companies_repo.get_company_by_id(company_id)
    hudu_id = str((company or {}).get("hudu_id") or "").strip()
    if not hudu_id:
        return False, "Configure the company's Hudu ID before creating privileged accounts."
    try:
        await hudu_service.validate_configuration()
    except hudu_service.HuduConfigurationError as exc:
        return False, f"Hudu is not ready for password storage: {exc}"
    roles = await _graph_get(graph_token, "https://graph.microsoft.com/v1.0/directoryRoles?$filter=displayName eq 'Global Administrator'&$select=id")
    if not (role_values := roles.get("value") or []):
        return False, "The Global Administrator directory role is not activated."
    members = await _graph_get_all(graph_token, f"https://graph.microsoft.com/v1.0/directoryRoles/{role_values[0]['id']}/members?$select=id")
    count = len(members)
    if 3 <= count <= 4:
        return True, "The tenant already meets the target of three Global Administrators."
    if count > 4:
        return False, "The tenant has more than four Global Administrators; remove excess assignments manually."
    domains = await _graph_get(graph_token, "https://graph.microsoft.com/v1.0/domains?$select=id,isDefault,isVerified")
    verified = [d for d in domains.get("value", []) if d.get("isVerified") and d.get("id")]
    domain = next((d["id"] for d in verified if d.get("isDefault")), None) or (verified[0]["id"] if verified else None)
    if not domain:
        return False, "No verified Microsoft 365 domain is available for the new accounts."
    created = 0
    for slot in range(1, 4 - count):
        suffix = secrets.token_hex(3)
        alias, password = f"myportal-emergency-admin-{slot}-{suffix}", _generate_emergency_admin_password()
        upn, user, assignment = f"{alias}@{domain}", None, None
        try:
            user = await _graph_post(graph_token, "https://graph.microsoft.com/v1.0/users", {
                "accountEnabled": True, "displayName": f"MyPortal Emergency Administrator {slot}",
                "mailNickname": alias, "userPrincipalName": upn,
                "passwordProfile": {"forceChangePasswordNextSignIn": True, "password": password}})
            assignment = await _post_app_role_assignment_with_retry(graph_token, "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments", {
                "principalId": user["id"], "roleDefinitionId": _GLOBAL_ADMIN_ROLE_DEFINITION_ID, "directoryScopeId": "/"})
            await hudu_service.create_asset_password(company_id=hudu_id, name=f"M365 Global Administrator – {upn}",
                username=upn, password=password, url="https://admin.microsoft.com/",
                description="Created automatically by MyPortal. Password change is required at first sign-in.")
            created += 1
        except Exception:
            # Compensating cleanup prevents an undocumented privileged identity.
            for resource in ([f"roleManagement/directory/roleAssignments/{assignment['id']}" if assignment and assignment.get("id") else None,
                              f"users/{user['id']}" if user and user.get("id") else None]):
                if resource:
                    try:
                        await _graph_delete(graph_token, f"https://graph.microsoft.com/v1.0/{resource}")
                    except Exception:
                        pass
            raise
    return True, f"Created {created} Global Administrator account(s) and stored each password separately in Hudu."


# ---------------------------------------------------------------------------
# EXO-based check and remediation helpers
# ---------------------------------------------------------------------------


async def _check_direct_send(exo_token: str, tenant_id: str) -> dict[str, Any]:
    """Check whether Direct Send (anonymous relay) is disabled via Exchange Online.

    Calls ``Get-OrganizationConfig`` and inspects the ``RejectDirectSend``
    property.  Requires the app to have ``Exchange.ManageAsApp`` and an
    Exchange Administrator RBAC role.
    """
    check_id = "bp_disable_direct_send"
    check_name = "Direct Send is disabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
        value = data.get("value") or []
        config: dict[str, Any] = value[0] if isinstance(value, list) and value else {}
        reject = config.get("RejectDirectSend")
        if reject is True:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_PASS,
                "details": "Direct Send (anonymous relay) is disabled.",
            }
        if reject is False:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_FAIL,
                "details": (
                    "Direct Send is enabled. External senders can relay mail "
                    "through your tenant without authentication."
                ),
            }
        return {
            "check_id": check_id,
            "check_name": check_name,
            "status": STATUS_UNKNOWN,
            "details": "Unable to determine Direct Send status from organization config.",
        }
    except M365Error as exc:
        return {
            "check_id": check_id,
            "check_name": check_name,
            "status": STATUS_UNKNOWN,
            "details": f"Unable to query Exchange Online organization config: {exc}",
        }


async def _run_direct_send_remediation(exo_token: str, tenant_id: str) -> bool:
    """Execute ``Set-OrganizationConfig -RejectDirectSend $true`` via EXO REST API.

    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        await _exo_invoke_command(
            exo_token,
            tenant_id,
            "Set-OrganizationConfig",
            {"RejectDirectSend": True},
        )
        return True
    except M365Error:
        return False


_IT_BASELINE_RULE_NAME = "Allow External Forward - IT Contacts"


def _it_baseline_config() -> tuple[list[dict[str, str]], str]:
    settings = get_settings()
    profiles = [
        {"contact": "Hawkins IT", "group": "IT", "alias": "it",
         "external": settings.m365_it_external_email_address.strip()},
        {"contact": "Hawkins IT Support", "group": "IT Support", "alias": "itsupport",
         "external": settings.m365_it_support_external_email_address.strip()},
    ]
    return profiles, settings.m365_it_recipient_address_contains_words.strip()


def _exo_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (response.get("value") or []) if isinstance(row, dict)]


def _smtp_value(value: Any) -> str:
    return str(value or "").removeprefix("SMTP:").removeprefix("smtp:").strip().lower()


def _exo_recipient_addresses(row: Mapping[str, Any]) -> set[str]:
    addresses = {
        _smtp_value(row.get("PrimarySmtpAddress")),
        _smtp_value(row.get("WindowsEmailAddress")),
        _smtp_value(row.get("ExternalEmailAddress")),
    }
    raw_addresses = row.get("EmailAddresses")
    if isinstance(raw_addresses, str):
        addresses.add(_smtp_value(raw_addresses))
    elif isinstance(raw_addresses, list):
        addresses.update(_smtp_value(address) for address in raw_addresses)
    return {address for address in addresses if address}


def _find_conflicting_recipient(
    recipients: list[dict[str, Any]],
    *,
    expected_name: str,
    expected_addresses: set[str],
    allowed_types: set[str],
) -> dict[str, Any] | None:
    expected_name_folded = expected_name.casefold()
    expected_addresses = {address.casefold() for address in expected_addresses if address}
    for row in recipients:
        recipient_type = str(row.get("RecipientTypeDetails") or row.get("RecipientType") or "").casefold()
        if recipient_type in allowed_types:
            continue
        candidate_names = {
            str(row.get("Name") or "").casefold(),
            str(row.get("DisplayName") or "").casefold(),
            str(row.get("Alias") or "").casefold(),
        }
        if expected_name_folded and expected_name_folded in candidate_names:
            return row
        if expected_addresses and _exo_recipient_addresses(row) & expected_addresses:
            return row
    return None


async def _inspect_it_contact_baseline(exo_token: str, tenant_id: str) -> dict[str, Any]:
    """Return desired baseline state, missing objects, and non-destructive conflicts."""
    profiles, recipient_words = _it_baseline_config()
    if not recipient_words or any(not profile["external"] for profile in profiles):
        return {"error": "Configure both M365 IT external addresses and the recipient match value."}
    if profiles[0]["external"].lower() == profiles[1]["external"].lower():
        return {"error": "The IT and IT Support external email addresses must be different."}

    domains = _exo_rows(await _exo_invoke_command(exo_token, tenant_id, "Get-AcceptedDomain"))
    default = next((row for row in domains if _coerce_exo_bool(row.get("Default"))
                    and str(row.get("DomainType") or "").lower() == "authoritative"), None)
    domain = str((default or {}).get("DomainName") or (default or {}).get("Name") or "").strip()
    if not domain or domain.lower().endswith(".onmicrosoft.com"):
        return {"error": "The default authoritative domain is missing or is an onmicrosoft.com domain."}

    contacts = _exo_rows(await _exo_invoke_command(exo_token, tenant_id, "Get-MailContact"))
    groups = _exo_rows(await _exo_invoke_command(exo_token, tenant_id, "Get-DistributionGroup"))
    recipients = _exo_rows(await _exo_invoke_command(exo_token, tenant_id, "Get-Recipient"))
    rules = _exo_rows(await _exo_invoke_command(exo_token, tenant_id, "Get-TransportRule"))
    missing: list[tuple[str, dict[str, str]]] = []
    conflicts: list[str] = []
    for profile in profiles:
        contact = next((row for row in contacts if str(row.get("Name") or "").casefold() == profile["contact"].casefold()), None)
        if contact is None:
            conflict = _find_conflicting_recipient(
                recipients,
                expected_name=profile["contact"],
                expected_addresses={profile["external"]},
                allowed_types={"mailcontact"},
            )
            if conflict is not None:
                conflicts.append(f'{profile["contact"]} mail contact conflicts with an existing Exchange recipient')
            else:
                missing.append(("contact", profile))
        elif (_smtp_value(contact.get("ExternalEmailAddress")) != profile["external"].lower()
              or not _coerce_exo_bool(contact.get("HiddenFromAddressListsEnabled"))):
            conflicts.append(f'{profile["contact"]} mail contact differs from the configured baseline')
        group = next((row for row in groups if str(row.get("Name") or "").casefold() == profile["group"].casefold()), None)
        desired_smtp = f'{profile["alias"]}@{domain}'.lower()
        if group is None:
            conflict = _find_conflicting_recipient(
                recipients,
                expected_name=profile["group"],
                expected_addresses={desired_smtp},
                allowed_types={"mailuniversalsecuritygroup", "mailuniversaldistributiongroup", "groupmailbox"},
            )
            if conflict is not None:
                conflicts.append(f'{profile["group"]} distribution group conflicts with an existing Exchange recipient')
            else:
                missing.append(("group", profile))
        elif (_smtp_value(group.get("PrimarySmtpAddress")) != desired_smtp
              or not _coerce_exo_bool(group.get("HiddenFromAddressListsEnabled"))
              or _coerce_exo_bool(group.get("RequireSenderAuthenticationEnabled"))):
            conflicts.append(f'{profile["group"]} distribution group differs from the configured baseline')
        else:
            members = _exo_rows(await _exo_invoke_command(
                exo_token, tenant_id, "Get-DistributionGroupMember", {"Identity": profile["group"]}
            ))
            member_addresses = {
                _smtp_value(row.get("PrimarySmtpAddress") or row.get("ExternalEmailAddress"))
                for row in members
            }
            if member_addresses != {profile["external"].lower()}:
                conflicts.append(f'{profile["group"]} distribution group membership differs from the configured baseline')

    rule = next((row for row in rules if str(row.get("Name") or "").casefold() == _IT_BASELINE_RULE_NAME.casefold()), None)
    desired_words = {recipient_words.casefold()}
    if rule is None:
        missing.append(("rule", {}))
    else:
        actual_words = rule.get("RecipientAddressContainsWords") or []
        if isinstance(actual_words, str):
            actual_words = [actual_words]
        if ({str(word).casefold() for word in actual_words} != desired_words
                or not _coerce_exo_bool(rule.get("StopRuleProcessing"))
                or not _coerce_exo_bool(rule.get("Enabled"))):
            conflicts.append(f'{_IT_BASELINE_RULE_NAME} transport rule differs from the configured baseline')
    return {"profiles": profiles, "recipient_words": recipient_words, "domain": domain,
            "missing": missing, "conflicts": conflicts}


async def _check_it_contact_baseline(exo_token: str, tenant_id: str) -> dict[str, Any]:
    check_id, check_name = "bp_it_contact_baseline", "IT contact forwarding baseline is configured"
    try:
        state = await _inspect_it_contact_baseline(exo_token, tenant_id)
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN, f"Unable to inspect Exchange Online: {exc}")
    if state.get("error"):
        return _result(check_id, check_name, STATUS_FAIL, state["error"])
    if state["conflicts"]:
        return _result(check_id, check_name, STATUS_FAIL, "; ".join(state["conflicts"]) + ". Existing objects are never overwritten.")
    if state["missing"]:
        labels = [kind for kind, _ in state["missing"]]
        return _result(check_id, check_name, STATUS_FAIL, f"The baseline is incomplete ({', '.join(labels)} missing). Run remediation to create only missing objects.")
    return _result(check_id, check_name, STATUS_PASS, "IT and IT Support contacts, groups, and external-forward transport rule match the configured baseline.")


async def _remediate_it_contact_baseline(exo_token: str, tenant_id: str) -> tuple[bool, str]:
    """Create missing baseline objects, refusing to modify any existing conflict."""
    state = await _inspect_it_contact_baseline(exo_token, tenant_id)
    if state.get("error"):
        return False, state["error"]
    if state["conflicts"]:
        return False, "; ".join(state["conflicts"]) + ". Resolve the conflict manually; no changes were made."

    while state["missing"]:
        kind, profile = state["missing"][0]
        try:
            if kind == "contact":
                await _exo_invoke_command(exo_token, tenant_id, "New-MailContact", {
                    "Name": profile["contact"], "ExternalEmailAddress": profile["external"]
                })
                await _exo_invoke_command(exo_token, tenant_id, "Set-MailContact", {
                    "Identity": profile["contact"], "HiddenFromAddressListsEnabled": True
                })
            elif kind == "group":
                await _exo_invoke_command(exo_token, tenant_id, "New-DistributionGroup", {
                    "Name": profile["group"], "Members": profile["external"],
                    "PrimarySmtpAddress": f'{profile["alias"]}@{state["domain"]}',
                    "RequireSenderAuthenticationEnabled": False,
                })
                await _exo_invoke_command(exo_token, tenant_id, "Set-DistributionGroup", {
                    "Identity": profile["group"], "HiddenFromAddressListsEnabled": True
                })
            elif kind == "rule":
                await _exo_invoke_command(exo_token, tenant_id, "New-TransportRule", {
                    "Name": _IT_BASELINE_RULE_NAME, "Priority": 0, "Enabled": True,
                    "RecipientAddressContainsWords": state["recipient_words"],
                    "StopRuleProcessing": True,
                })
        except M365Error as exc:
            if exc.http_status != 409:
                raise
            refreshed_state = await _inspect_it_contact_baseline(exo_token, tenant_id)
            if refreshed_state.get("error"):
                return False, refreshed_state["error"]
            if refreshed_state["conflicts"]:
                return False, "; ".join(refreshed_state["conflicts"]) + ". Resolve the conflict manually; no changes were made."
            if (kind, profile) in refreshed_state["missing"]:
                raise
            state = refreshed_state
            continue

        state = await _inspect_it_contact_baseline(exo_token, tenant_id)
        if state.get("error"):
            return False, state["error"]
        if state["conflicts"]:
            return False, "; ".join(state["conflicts"]) + ". Resolve the conflict manually; no changes were made."
    return True, "Created the missing IT contact forwarding baseline objects."


_REPORT_SETTINGS_URL = "https://graph.microsoft.com/v1.0/admin/reportSettings"
_AUTHORIZATION_POLICY_URL = "https://graph.microsoft.com/v1.0/policies/authorizationPolicy"
# guestUserRoleId: Guest user (most restrictive) – no directory read access
_GUEST_ROLE_ID_MOST_RESTRICTIVE = "10dae51f-b6af-4016-8d66-8c2a99b929b3"


async def _check_concealed_names(token: str) -> dict[str, Any]:
    """Check whether concealed names are displayed in Microsoft 365 usage reports.

    Calls ``GET /admin/reportSettings`` and inspects the ``displayConcealedNames``
    property.  When ``displayConcealedNames`` is ``True`` the tenant has opted to
    show real user, group, and site names in reports (the best-practice
    recommendation); when it is ``False`` obfuscated names are shown instead.

    Requires the ``ReportSettings.ReadWrite.All`` Graph application permission.
    The ``/admin/reportSettings`` endpoint rejects tokens that lack this specific
    permission with ``S2SUnauthorized / Invalid permission`` (403), even when the
    token carries ``Reports.Read.All``.
    """
    check_id = "bp_concealed_names"
    check_name = "Concealed user, group, and site names in all reports is disabled"
    try:
        data = await _graph_get(token, _REPORT_SETTINGS_URL)
        display_concealed = data.get("displayConcealedNames")
        if display_concealed is False:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_PASS,
                "details": "Report settings are configured to display real user, group, and site names.",
            }
        if display_concealed is True:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_FAIL,
                "details": (
                    "Report settings are configured to conceal user, group, and site names. "
                    "Disable name concealment to improve report usability and auditability."
                ),
            }
        return {
            "check_id": check_id,
            "check_name": check_name,
            "status": STATUS_UNKNOWN,
            "details": "Unable to determine report settings concealed names status.",
        }
    except M365Error as exc:
        if exc.http_status == 403:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_UNKNOWN,
                "details": (
                    "The enterprise app is missing the ReportSettings.ReadWrite.All permission "
                    "required to read /admin/reportSettings. To fix this: on the M365 settings "
                    "page, click 'Authorise portal access' to re-grant the required permissions."
                ),
            }
        return {
            "check_id": check_id,
            "check_name": check_name,
            "status": STATUS_UNKNOWN,
            "details": f"Unable to query report settings: {exc}",
        }


# ---------------------------------------------------------------------------
# Helpers for new best-practice checks
# ---------------------------------------------------------------------------
#
# The helpers below cover the second-wave checks added per the approved
# expansion plan.  They follow the same conventions as the original CIS /
# best-practice helpers:
#
# * Each function takes ``token: str`` (Graph) or ``(exo_token, tenant_id)``.
# * Each returns a dict with ``check_id``, ``check_name``, ``status`` and
#   ``details`` (and only those keys – per-entry catalog metadata such as
#   ``has_remediation`` is enriched from the catalog later).
# * Graph errors are caught and translated into ``STATUS_UNKNOWN`` so the
#   runner does not see ``M365Error`` propagate; the runner has its own
#   retry/transient-error handling on top of that.

_AUTH_METHODS_POLICY_URL = (
    "https://graph.microsoft.com/beta/policies/authenticationMethodsPolicy"
)
_DOMAINS_URL = "https://graph.microsoft.com/v1.0/domains"
_DIRECTORY_ROLES_URL = "https://graph.microsoft.com/v1.0/directoryRoles"
_DIRECTORY_ROLES_WITH_MEMBERS_URL = (
    "https://graph.microsoft.com/v1.0/directoryRoles"
    "?$select=id,displayName&$top=999"
)
_AUTHENTICATION_REQUIREMENTS_URL_TMPL = (
    "https://graph.microsoft.com/beta/users/{user_id}/authentication/requirements"
)
# Lowercased policy states considered actively configured for remediation gating.
# Original Graph values: "enabled", "enabledForReportingButNotEnforced".
_ACTIVE_CONDITIONAL_ACCESS_POLICY_STATES_LOWER = frozenset(
    {"enabled", "enabledforreportingbutnotenforced"}
)
_USERS_LIST_URL = (
    "https://graph.microsoft.com/v1.0/users"
    "?$select=id,displayName,userPrincipalName,userType,onPremisesSyncEnabled"
    ",accountEnabled,assignedLicenses"
    "&$top=999"
)
_GROUPS_LIST_URL = (
    "https://graph.microsoft.com/v1.0/groups"
    "?$select=id,displayName,visibility,groupTypes,membershipRule"
    "&$top=999"
)
_GROUP_URL_TMPL = "https://graph.microsoft.com/v1.0/groups/{group_id}"
_CA_POLICIES_URL = (
    "https://graph.microsoft.com/v1.0/identity/conditionalAccess/policies"
)
_ACCESS_REVIEWS_URL = (
    "https://graph.microsoft.com/v1.0/identityGovernance/accessReviews/definitions"
)
_PIM_ASSIGNMENTS_URL = (
    "https://graph.microsoft.com/v1.0/roleManagement/directory/"
    "roleEligibilityScheduleInstances?$top=999"
)
_PIM_POLICIES_URL = (
    "https://graph.microsoft.com/v1.0/policies/roleManagementPolicyAssignments"
    "?$filter=scopeId eq '/' and scopeType eq 'DirectoryRole'"
)
_USER_REGISTRATION_DETAILS_URL = (
    "https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails"
    "?$top=999"
)
_DEVICE_REG_POLICY_URL = (
    "https://graph.microsoft.com/beta/policies/deviceRegistrationPolicy"
)
_FORMS_ADMIN_URL = "https://graph.microsoft.com/beta/admin/forms"
_FORMS_PERMISSION_NAME = "OrgSettings-Forms.ReadWrite.All"
_DIRECTORY_SETTINGS_URL = "https://graph.microsoft.com/beta/groupSettings"
_SECURITY_DEFAULTS_URL = (
    "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy"
)
_SPO_SETTINGS_URL = "https://graph.microsoft.com/v1.0/admin/sharepoint/settings"
_EWS_EXO_APP_ID = "00000002-0000-0ff1-ce00-000000000000"
_EWS_FULL_ACCESS_AS_APP_ROLE = "e4a3c0d2-0003-4b45-8fd7-d8e34591ad28"
_EWS_USAGE_REPORT_URL = (
    "https://graph.microsoft.com/beta/reports/"
    "getApiUsage(period='D30',serviceArea='Microsoft Exchange')"
)
_APP_ID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Well-known directory role template IDs used by several checks
_ROLE_TEMPLATE_GLOBAL_ADMIN = "62e90394-69f5-4237-9190-012177145e10"
_ROLE_TEMPLATE_PRIVILEGED_ROLE_ADMIN = "e8611ab8-c189-46e8-94e1-60213ab1f814"
_ROLE_TEMPLATE_SECURITY_ADMIN = "194ae4cb-b126-40b2-bd5b-6091b380977d"
_ROLE_TEMPLATE_EXCHANGE_ADMIN = "29232cdf-9323-42fd-ade2-1d097af3e4de"
_ROLE_TEMPLATE_BILLING_ADMIN = "b0f54661-2d74-4c50-afa3-1ec803f12efe"
_ADMIN_ROLE_TEMPLATES = {
    _ROLE_TEMPLATE_GLOBAL_ADMIN,
    _ROLE_TEMPLATE_PRIVILEGED_ROLE_ADMIN,
    _ROLE_TEMPLATE_SECURITY_ADMIN,
    _ROLE_TEMPLATE_EXCHANGE_ADMIN,
    _ROLE_TEMPLATE_BILLING_ADMIN,
}

# Phishing-resistant MFA built-in authentication strength
# Microsoft Authenticator feature settings that defeat MFA-fatigue / consent
# spam: number matching, app-context, and location-context displays.  Keep in
# sync with /authenticationMethodConfigurations/MicrosoftAuthenticator.
_MFA_FATIGUE_PROTECTION_KEYS: tuple[str, ...] = (
    "numberMatchingRequiredState",
    "displayAppInformationRequiredState",
    "displayLocationInformationRequiredState",
)

# Graph no longer accepts numberMatchingRequiredState inside featureSettings
# PATCH payloads for MicrosoftAuthenticator.  Automation can still enable the
# app/location context prompts, but number matching must be turned on manually.
_MFA_FATIGUE_PATCHABLE_KEYS: tuple[str, ...] = (
    "displayAppInformationRequiredState",
    "displayLocationInformationRequiredState",
)
_MFA_FATIGUE_MANUAL_ONLY_KEYS: tuple[str, ...] = ("numberMatchingRequiredState",)
_MFA_FATIGUE_NUMBER_MATCHING_MANUAL_MESSAGE = (
    "Microsoft Graph no longer supports toggling Microsoft Authenticator number "
    "matching in featureSettings. Enable Number matching manually in Entra, "
    "then re-evaluate the check."
)
_MFA_FATIGUE_NUMBER_MATCHING_PARTIAL_MESSAGE = (
    "Supported Microsoft Authenticator app/location prompts were updated "
    "automatically, but Microsoft Graph no longer supports toggling number "
    "matching in featureSettings. Enable Number matching manually in Entra, "
    "then re-evaluate the check."
)

_MFA_FATIGUE_REMEDIATION_PAYLOAD: dict[str, Any] = {
    # Graph's update contract requires the concrete configuration type.  It
    # can return 204 while silently retaining nested feature settings when the
    # type discriminators are omitted, which makes the verification below
    # report that app and location context are still disabled.
    "@odata.type": (
        "#microsoft.graph.microsoftAuthenticatorAuthenticationMethodConfiguration"
    ),
    "featureSettings": {
        "@odata.type": "#microsoft.graph.microsoftAuthenticatorFeatureSettings",
        **{
            setting: {
                "@odata.type": "#microsoft.graph.authenticationMethodFeatureConfiguration",
                "state": "enabled",
                "includeTarget": {
                    "@odata.type": "#microsoft.graph.featureTarget",
                    "targetType": "group",
                    "id": "all_users",
                },
            }
            for setting in _MFA_FATIGUE_PATCHABLE_KEYS
        },
    }
}

# Authentication-method policy updates can be eventually consistent.  Verify
# this remediation before reporting success so the subsequent UI refresh does
# not persist the stale pre-remediation state returned immediately after PATCH.
_MFA_FATIGUE_VERIFICATION_ATTEMPTS = 3

# SMS / Voice / Email authentication-method updates are also eventually
# consistent. Verify that all weak methods are disabled before reporting
# success so the UI does not immediately re-show a stale failure.
_WEAK_AUTH_METHODS_VERIFICATION_ATTEMPTS = 3

# Microsoft Forms settings updates can also be eventually consistent. Verify
# remediation before reporting success so stale reads do not show a false fail.
# Use more attempts than the default (5 × exponential back-off = up to ~15 s)
# because the /beta/admin/forms/settings endpoint propagates slowly.
_FORMS_PHISHING_VERIFICATION_ATTEMPTS = 5


_PHISHING_RESISTANT_AUTH_STRENGTH_ID = "00000000-0000-0000-0000-000000000004"

# Maximum admin browser session length (in hours) for the sign-in frequency
# best-practice; CIS recommends ≤ 4 hours for privileged role browser sessions.
_ADMIN_SIGNIN_FREQ_MAX_HOURS = 4


async def _safe_graph_get(token: str, url: str) -> dict[str, Any] | None:
    """GET a Graph URL, swallowing M365Error and returning None on failure."""
    try:
        return await _graph_get(token, url)
    except M365Error:
        return None


async def _safe_graph_get_all(token: str, url: str) -> list[dict[str, Any]] | None:
    """Paginated GET, swallowing M365Error and returning None on failure."""
    try:
        return await _graph_get_all(token, url)
    except M365Error:
        return None


def _extract_app_ids(value: Any) -> list[str]:
    """Return unique, lower-cased AppIDs found in *value*."""
    text = ""
    if isinstance(value, list):
        text = ",".join(str(item or "") for item in value)
    elif value is not None:
        text = str(value)
    seen: set[str] = set()
    app_ids: list[str] = []
    for match in _APP_ID_PATTERN.finditer(text):
        app_id = match.group(0).lower()
        if app_id not in seen:
            seen.add(app_id)
            app_ids.append(app_id)
    return app_ids


def _format_app_label(app_id: str, display_name: str | None, *, suffix: str | None = None) -> str:
    label = (display_name or "").strip() or "Unknown application"
    if suffix:
        return f"{label} ({app_id}, {suffix})"
    return f"{label} ({app_id})"


def _format_ews_enabled(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "not explicitly set"
    return "$true" if _coerce_exo_bool(value) else "$false"


def _csv_row_value(row: Mapping[str, Any], *names: str) -> str:
    normalised = {
        re.sub(r"\s+", " ", str(key or "").strip().lower()): str(value or "").strip()
        for key, value in row.items()
    }
    for name in names:
        value = normalised.get(re.sub(r"\s+", " ", name.strip().lower()), "")
        if value:
            return value
    return ""


async def _download_graph_csv_report(access_token: str, url: str) -> list[dict[str, str]]:
    headers = {
        "Authorization": "Bearer " + access_token,
        "Accept": "text/csv",
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
            if response.status_code in (301, 302, 303, 307, 308):
                download_url = str(response.headers.get("Location") or "").strip()
                if not download_url:
                    raise M365Error("Microsoft Graph report export missing download URL")
                csv_response = await client.get(download_url)
            else:
                csv_response = response
    except httpx.TimeoutException as exc:
        raise M365Error(
            f"Microsoft Graph report request timed out ({type(exc).__name__})"
        ) from exc
    except httpx.NetworkError as exc:
        raise M365Error(
            f"Microsoft Graph report network error ({type(exc).__name__})"
        ) from exc

    if csv_response.status_code != 200:
        raise M365Error(
            f"Microsoft Graph report request failed ({csv_response.status_code})",
            http_status=csv_response.status_code,
        )

    csv_text = csv_response.text
    if "\x00" in csv_text:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                csv_text = csv_response.content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        filtered = {str(key): str(value or "") for key, value in row.items() if key is not None}
        if not filtered:
            continue
        if "sep=" in next(iter(filtered)).lower() and len(filtered) == 1:
            continue
        rows.append(filtered)
    return rows


async def _resolve_app_display_names(
    graph_token: str, app_ids: list[str]
) -> dict[str, str | None]:
    resolved: dict[str, str | None] = {}
    for app_id in app_ids:
        try:
            data = await _graph_get(
                graph_token,
                (
                    "https://graph.microsoft.com/v1.0/servicePrincipals"
                    f"?$filter=appId eq '{app_id}'&$select=appId,displayName"
                ),
            )
        except M365Error:
            resolved[app_id] = None
            continue
        rows = data.get("value") or []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        name = str(row.get("displayName") or "").strip()
        resolved[app_id] = name or None
    return resolved


async def _get_ews_permission_inventory(graph_token: str) -> tuple[list[dict[str, str]], list[str]]:
    service_principals = await _graph_get(
        graph_token,
        (
            "https://graph.microsoft.com/v1.0/servicePrincipals"
            f"?$filter=appId eq '{_EWS_EXO_APP_ID}'&$select=id"
        ),
    )
    exo_entries = service_principals.get("value") or []
    exo_sp_id = str((exo_entries[0] or {}).get("id") or "").strip() if exo_entries else ""
    if not exo_sp_id:
        raise M365Error("Exchange Online service principal not found in tenant")

    assignments = await _graph_get_all(
        graph_token,
        (
            "https://graph.microsoft.com/v1.0/servicePrincipals/"
            f"{exo_sp_id}/appRoleAssignedTo"
            "?$select=appRoleId,principalId,principalType,principalDisplayName&$top=999"
        ),
    )
    apps: list[dict[str, str]] = []
    unresolved: list[str] = []
    for assignment in assignments:
        if str(assignment.get("principalType") or "").strip().lower() != "serviceprincipal":
            continue
        app_role_id = str(assignment.get("appRoleId") or "").strip().lower()
        if app_role_id != _EWS_FULL_ACCESS_AS_APP_ROLE:
            continue
        principal_id = str(assignment.get("principalId") or "").strip()
        fallback_name = str(assignment.get("principalDisplayName") or "").strip()
        try:
            principal = await _graph_get(
                graph_token,
                (
                    "https://graph.microsoft.com/v1.0/servicePrincipals/"
                    f"{principal_id}?$select=appId,displayName"
                ),
            )
        except M365Error:
            label = fallback_name or principal_id or "unknown service principal"
            unresolved.append(label)
            continue
        app_id = str(principal.get("appId") or "").strip().lower()
        display_name = str(principal.get("displayName") or fallback_name or "").strip()
        if not app_id:
            unresolved.append(display_name or principal_id or "unknown service principal")
            continue
        apps.append(
            {
                "app_id": app_id,
                "display_name": display_name or "Unknown application",
            }
        )

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for app in apps:
        app_id = app["app_id"]
        if app_id in seen:
            continue
        seen.add(app_id)
        deduped.append(app)
    deduped.sort(key=lambda item: ((item.get("display_name") or "").lower(), item["app_id"]))
    return deduped, sorted(set(unresolved))


async def _get_ews_usage_apps(
    delegated_token: str,
) -> list[dict[str, str | int]]:
    rows = await _download_graph_csv_report(delegated_token, _EWS_USAGE_REPORT_URL)
    usage_by_app: dict[str, dict[str, str | int]] = {}
    for row in rows:
        protocol = _csv_row_value(
            row,
            "Protocol",
            "Protocol Name",
            "API",
            "Api",
            "API Family",
            "Feature",
            "Workload",
        )
        if protocol:
            if "ews" not in protocol.lower():
                continue
        elif not any("ews" in str(key or "").lower() for key in row):
            continue
        app_ids = _extract_app_ids(
            _csv_row_value(row, "AppId", "Application Id", "ApplicationID", "Client Id")
        )
        if not app_ids:
            continue
        app_id = app_ids[0]
        usage_raw = _csv_row_value(row, "Usage", "Calls", "Successful Requests", "Count")
        try:
            usage = int(float(usage_raw or "0"))
        except ValueError:
            usage = 0
        last_seen = _csv_row_value(row, "Date", "Last Activity Date", "Report Refresh Date")
        existing = usage_by_app.setdefault(
            app_id,
            {"app_id": app_id, "usage": 0, "last_seen": ""},
        )
        existing["usage"] = int(existing.get("usage") or 0) + max(usage, 0)
        if last_seen and last_seen > str(existing.get("last_seen") or ""):
            existing["last_seen"] = last_seen

    return [
        usage_by_app[app_id]
        for app_id in sorted(usage_by_app)
    ]


async def _get_stored_best_practice_notes(company_id: int, check_id: str) -> str:
    rows = await bp_repo.list_results(company_id)
    row = next((item for item in rows if item.get("check_id") == check_id), None)
    return str((row or {}).get("notes") or "")


async def _collect_ews_dependency_state(
    graph_token: str, company_id: int
) -> dict[str, Any]:
    check_id = "bp_ews_required_apps_allowed"
    exo_token, tenant_id = await _acquire_exo_access_token(company_id)
    config = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    org = _exo_first_value(config)
    current_allowed = _extract_app_ids(org.get("EwsAllowedAppIDs"))
    allowed_set = set(current_allowed)

    permission_apps, unresolved_permission_apps = await _get_ews_permission_inventory(
        graph_token
    )

    usage_apps: list[dict[str, str | int]] = []
    usage_error: str | None = None
    try:
        delegated_token = await acquire_delegated_token(company_id)
    except Exception:  # noqa: BLE001 - actionable state reported below
        delegated_token = None
    if not delegated_token:
        usage_error = (
            "EWS usage data is unavailable. Re-authorise portal access with a "
            "Global Reader or Global Administrator account so the delegated "
            "Reports.Read.All report can be queried."
        )
    else:
        try:
            usage_apps = await _get_ews_usage_apps(delegated_token)
        except M365Error as exc:
            usage_error = (
                "EWS usage data could not be read from Microsoft 365 usage reports: "
                f"{exc}"
            )

    notes = await _get_stored_best_practice_notes(company_id, check_id)
    approved_note_ids = _extract_app_ids(notes)

    permission_by_id = {app["app_id"]: dict(app) for app in permission_apps}
    observed_ids = [str(app["app_id"]) for app in usage_apps if app.get("app_id")]

    names_to_resolve = sorted(
        (set(observed_ids) | set(approved_note_ids))
        - set(permission_by_id)
    )
    resolved_names = await _resolve_app_display_names(graph_token, names_to_resolve)

    observed_apps: list[dict[str, str]] = []
    for app in usage_apps:
        app_id = str(app["app_id"])
        display_name = permission_by_id.get(app_id, {}).get("display_name") or resolved_names.get(app_id)
        observed_apps.append(
            {
                "app_id": app_id,
                "display_name": str(display_name or "Unknown application"),
                "usage": str(app.get("usage") or 0),
                "last_seen": str(app.get("last_seen") or ""),
            }
        )

    approved_note_apps: list[dict[str, str]] = []
    for app_id in approved_note_ids:
        if app_id in {app["app_id"] for app in observed_apps}:
            continue
        display_name = permission_by_id.get(app_id, {}).get("display_name") or resolved_names.get(app_id)
        approved_note_apps.append(
            {
                "app_id": app_id,
                "display_name": str(display_name or "Unknown application"),
            }
        )

    required_apps = observed_apps + approved_note_apps
    missing_required_apps = [
        app for app in required_apps if app["app_id"] not in allowed_set
    ]
    permission_only_apps = [
        app
        for app in permission_apps
        if app["app_id"] not in {item["app_id"] for item in required_apps}
    ]

    return {
        "config": org,
        "exo_token": exo_token,
        "tenant_id": tenant_id,
        "ews_enabled": org.get("EwsEnabled"),
        "current_allowed": current_allowed,
        "required_apps": required_apps,
        "observed_apps": observed_apps,
        "approved_note_apps": approved_note_apps,
        "permission_only_apps": permission_only_apps,
        "missing_required_apps": missing_required_apps,
        "unresolved_permission_apps": unresolved_permission_apps,
        "usage_error": usage_error,
    }


async def _get_directory_role_member_ids(token: str) -> set[str] | None:
    """Return IDs for accounts assigned to any active directory role.

    Unlicensed administrator accounts can look identical to shared mailboxes in
    the Graph users response.  Enumerating active directory roles prevents the
    shared-mailbox check (and, critically, its remediation) from treating those
    privileged identities as mailboxes.  ``None`` is returned if role data is
    incomplete so callers can fail closed rather than risk disabling an admin.
    """
    roles = await _safe_graph_get_all(token, _DIRECTORY_ROLES_WITH_MEMBERS_URL)
    if roles is None:
        return None

    member_ids: set[str] = set()
    for role in roles:
        role_id = str(role.get("id") or "").strip()
        if not role_id:
            continue
        members = await _safe_graph_get_all(
            token,
            f"https://graph.microsoft.com/v1.0/directoryRoles/{role_id}/"
            "transitiveMembers/microsoft.graph.user"
            "?$select=id&$top=999",
        )
        if members is None:
            return None
        member_ids.update(
            str(member.get("id"))
            for member in members
            if member.get("id")
        )
    return member_ids


def _result(
    check_id: str, check_name: str, status: str, details: str,
    affected_accounts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    result = {
        "check_id": check_id,
        "check_name": check_name,
        "status": status,
        "details": details,
    }
    if affected_accounts is not None:
        result["affected_accounts"] = affected_accounts
    return result


def _account_finding(account: dict[str, Any], *, identity_key: str = "id") -> dict[str, str]:
    """Build a safe, stable account reference for persistence and display."""
    account_id = str(account.get(identity_key) or account.get("UserPrincipalName") or account.get("Identity") or "").strip()
    label = str(
        account.get("userPrincipalName") or account.get("UserPrincipalName")
        or account.get("displayName") or account.get("DisplayName")
        or account.get("Identity") or account_id
    ).strip()
    return {"id": account_id, "name": label}


async def _apply_account_exclusions(
    company_id: int, check_id: str, status: str, details: str,
    affected_accounts: list[dict[str, str]],
) -> tuple[str, str, list[dict[str, str]]]:
    """Mark account findings excluded and calculate the effective check status."""
    exclusions = await bp_repo.get_account_exclusions(company_id, check_id)
    excluded_ids = {account_id for _, account_id in exclusions}
    accounts = [
        {**account, "excluded": str(account.get("id") or "") in excluded_ids}
        for account in affected_accounts
        if account.get("id") and account.get("name")
    ]
    active = [account for account in accounts if not account["excluded"]]
    excluded_count = len(accounts) - len(active)
    if accounts and not active:
        return STATUS_PASS, f"All {len(accounts)} listed account finding(s) are excluded for this check.", accounts
    if excluded_count:
        names = ", ".join(account["name"] for account in active[:5])
        suffix = "" if len(active) <= 5 else f" (and {len(active) - 5} more)"
        details = (
            f"{len(active)} account(s) require attention: {names}{suffix}. "
            f"{excluded_count} account(s) excluded for this check."
        )
    return status, details, accounts


def _manual_review_factory(
    check_id: str, check_name: str, instructions: str
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return a runner that always yields STATUS_UNKNOWN with manual instructions.

    Used for checks whose source surface (Teams PowerShell, Security &
    Compliance PowerShell, on-prem AD) requires infrastructure beyond the
    current Graph/EXO clients.  Admins see the catalog entry, the full
    remediation script, and a clear note that manual verification is required.
    """

    async def _runner(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _result(check_id, check_name, STATUS_UNKNOWN, instructions)

    _runner.__name__ = f"_check_{check_id}_manual"
    return _runner


# ---------------------------------------------------------------------------
# DNS-over-HTTPS helper (used by SPF / DMARC checks)
# ---------------------------------------------------------------------------

async def _dns_txt_records(domain: str) -> list[str] | None:
    """Return all TXT record strings for *domain* using Google DNS-over-HTTPS.

    Returns ``None`` if the lookup fails for any reason.  Each element of the
    returned list is the full quoted TXT record value (with enclosing quotes
    stripped), e.g. ``"v=spf1 include:spf.protection.outlook.com -all"``.
    """
    domain = domain.rstrip(".")
    url = f"https://dns.google/resolve?name={domain}&type=TXT"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Accept": "application/dns-json"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        answers = data.get("Answer") or []
        records: list[str] = []
        for ans in answers:
            rdata = str(ans.get("data") or "").strip()
            if rdata.startswith('"') and rdata.endswith('"'):
                rdata = rdata[1:-1]
            if rdata:
                records.append(rdata)
        return records
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# SharePoint Online checks (via Microsoft Graph /admin/sharepoint/settings)
# ---------------------------------------------------------------------------

_SPO_MISSING_PERM_MSG = (
    "The enterprise app is missing the SharePointTenantSettings.Read.All "
    "permission required to read /admin/sharepoint/settings. "
    "Re-authorise portal access on the M365 settings page to grant this permission."
)


async def _get_spo_settings(token: str) -> dict[str, Any] | None:
    """Fetch the SharePoint Online tenant settings from the Graph API."""
    try:
        return await _graph_get(token, _SPO_SETTINGS_URL)
    except M365Error:
        return None


async def _check_external_content_sharing_restricted(token: str) -> dict[str, Any]:
    check_id = "bp_external_content_sharing_restricted"
    check_name = "External content sharing is restricted"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    capability = str(settings.get("sharingCapability") or "").lower()
    if not capability:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read sharingCapability from SharePoint tenant settings.")
    passing = {"disabled", "existingexternalusersharingonly"}
    if capability in passing:
        return _result(check_id, check_name, STATUS_PASS,
                       f"SharePoint tenant sharing capability is '{capability}'.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"SharePoint tenant sharing capability is '{capability}'; "
                   "restrict to 'ExistingExternalUserSharingOnly' or 'Disabled'.")


async def _check_sp_guests_cannot_share_unowned(token: str) -> dict[str, Any]:
    check_id = "bp_sp_guests_cannot_share_unowned"
    check_name = "SharePoint guest users cannot share items they don't own"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    # Graph property isResharingByExternalUsersEnabled is the inverse of
    # SPO PowerShell's PreventExternalUsersFromResharing: True means resharing
    # IS allowed (bad), False means it is blocked (good).
    resharing = settings.get("isResharingByExternalUsersEnabled")
    if resharing is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read isResharingByExternalUsersEnabled from SharePoint tenant settings.")
    if resharing is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "External users cannot re-share items they do not own (resharing is disabled).")
    return _result(check_id, check_name, STATUS_FAIL,
                   "External users can re-share items they do not own. "
                   "Run: Set-SPOTenant -PreventExternalUsersFromResharing $true")


async def _check_onedrive_content_sharing_restricted(token: str) -> dict[str, Any]:
    check_id = "bp_onedrive_content_sharing_restricted"
    check_name = "OneDrive content sharing is restricted"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    # The Graph API exposes the OneDrive sharing capability under
    # oneDriveSharingCapability (may appear alongside v1.0 fields)
    capability = str(settings.get("oneDriveSharingCapability") or "").lower()
    if not capability:
        # Fall back to the tenant-wide sharingCapability as an indicator
        tenant_cap = str(settings.get("sharingCapability") or "").lower()
        if not tenant_cap:
            return _result(check_id, check_name, STATUS_UNKNOWN,
                           "Unable to read OneDrive sharing capability from SharePoint tenant settings.")
        capability = tenant_cap
    passing = {"disabled", "existingexternalusersharingonly"}
    if capability in passing:
        return _result(check_id, check_name, STATUS_PASS,
                       f"OneDrive sharing capability is '{capability}'.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"OneDrive sharing capability is '{capability}'; "
                   "restrict to 'ExistingExternalUserSharingOnly' or 'Disabled'. "
                   "Run: Set-SPOTenant -OneDriveSharingCapability ExistingExternalUserSharingOnly")


async def _check_link_sharing_restricted_spo_od(token: str) -> dict[str, Any]:
    check_id = "bp_link_sharing_restricted_spo_od"
    check_name = "Link sharing is restricted in SharePoint and OneDrive"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    link_type = str(settings.get("defaultSharingLinkType") or "").lower()
    link_perm = str(settings.get("defaultLinkPermission") or "").lower()
    if not link_type and not link_perm:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read defaultSharingLinkType/defaultLinkPermission from SharePoint tenant settings.")
    issues: list[str] = []
    if link_type and link_type not in {"direct", "none"}:
        issues.append(f"defaultSharingLinkType is '{link_type}' (should be 'direct')")
    if link_perm and link_perm not in {"view", "none"}:
        issues.append(f"defaultLinkPermission is '{link_perm}' (should be 'view')")
    if not issues:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Default sharing link type is '{link_type}' with '{link_perm}' permission.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Sharing link defaults are too permissive: " + "; ".join(issues) + ". "
                   "Run: Set-SPOTenant -DefaultSharingLinkType Direct -DefaultLinkPermission View")


async def _check_modern_auth_sp_apps(token: str) -> dict[str, Any]:
    check_id = "bp_modern_auth_sp_apps"
    check_name = "Modern authentication for SharePoint applications is required"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    legacy = settings.get("isLegacyAuthProtocolsEnabled")
    if legacy is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read isLegacyAuthProtocolsEnabled from SharePoint tenant settings.")
    if legacy is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "Legacy authentication protocols are disabled for SharePoint Online.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Legacy authentication protocols are enabled for SharePoint Online. "
                   "Run: Set-SPOTenant -LegacyAuthProtocolsEnabled $false")


async def _check_sharepoint_infected_files_block(token: str) -> dict[str, Any]:
    check_id = "bp_sharepoint_infected_files_block"
    check_name = "Office 365 SharePoint infected files are disallowed for download"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    # The Graph API property may appear as isDisableInfectedFileDownload or
    # preventDownloadForInfectedFiles depending on the API version
    disallow = settings.get("isDisableInfectedFileDownload")
    if disallow is None:
        disallow = settings.get("preventDownloadForInfectedFiles")
    if disallow is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read infected-file download restriction from SharePoint tenant settings. "
                       "Run: Get-SPOTenant | Select DisallowInfectedFileDownload to verify manually.")
    if disallow is True:
        return _result(check_id, check_name, STATUS_PASS,
                       "Infected file download is blocked in SharePoint/OneDrive.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Infected files can be downloaded from SharePoint/OneDrive. "
                   "Run: Set-SPOTenant -DisallowInfectedFileDownload $true")


async def _check_sharepoint_sign_out_inactive_users(token: str) -> dict[str, Any]:
    check_id = "bp_sharepoint_sign_out_inactive_users"
    check_name = "Inactive users are signed out of SharePoint Online"
    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)
    enabled = settings.get("idleSignOutEnabled")
    if enabled is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read idleSignOutEnabled from SharePoint tenant settings. "
                       "Run: Get-SPOTenant | Select SignOutInactiveUsersAfter to verify manually.")
    if not enabled:
        return _result(check_id, check_name, STATUS_FAIL,
                       "Idle session sign-out is not enabled for SharePoint Online. "
                       "Run: Set-SPOTenant -SignOutInactiveUsersAfter 01:00:00")
    # CIS recommends the combined timeout (warn + sign-out) does not exceed 1 hour (3600 s).
    warn_secs = settings.get("idleSignOutWarnAfterSeconds") or 0
    signout_secs = settings.get("idleSignOutSignOutAfterSeconds") or 0
    total_secs = int(warn_secs) + int(signout_secs)
    if total_secs > 3600:
        return _result(check_id, check_name, STATUS_FAIL,
                       f"Idle session sign-out is enabled but the total timeout "
                       f"({total_secs // 60} min) exceeds the recommended 60 minutes. "
                       "Run: Set-SPOTenant -SignOutInactiveUsersAfter 01:00:00")
    return _result(check_id, check_name, STATUS_PASS,
                   f"Idle session sign-out is enabled with a total timeout of "
                   f"{total_secs // 60} min for SharePoint Online.")


# URL for listing organisation-level directory settings (includes password
# protection and banned-password configuration).
_ORG_SETTINGS_URL = "https://graph.microsoft.com/v1.0/settings"

# Template ID for the "Password Rule Settings" directory setting template.
_PASSWORD_RULE_SETTINGS_TEMPLATE_ID = "5cf42378-d67d-4f36-ba46-e8b86229381d"

# Endpoint for listing SharePoint sites (search=* returns all sites).
_SPO_SITES_URL = (
    "https://graph.microsoft.com/v1.0/sites"
    "?search=*&$select=id,displayName,webUrl,sharingCapability&$top=200"
)


async def _check_sharepoint_external_sharing_restricted(token: str) -> dict[str, Any]:
    """Check that SharePoint site-level sharing is restricted.

    Uses the Graph API SharePoint admin settings to determine the tenant-level
    sharing capability.  If the tenant is already maximally restrictive
    (``disabled`` or ``existingExternalUserSharingOnly``) then per-site
    capabilities cannot exceed that ceiling and the check passes.

    When the tenant allows ``externalUserAndGuestSharing``, we enumerate sites
    via the Graph API and flag any site whose ``sharingCapability`` is set to
    the most permissive value.  If the Graph API does not return the
    ``sharingCapability`` property for individual sites the check falls back to
    marking the tenant as failing because sites may be configured permissively.
    """
    check_id = "bp_sharepoint_external_sharing_restricted"
    check_name = "SharePoint external sharing is restricted"

    settings = await _get_spo_settings(token)
    if settings is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, _SPO_MISSING_PERM_MSG)

    tenant_cap = str(settings.get("sharingCapability") or "").lower()
    if not tenant_cap:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to read sharingCapability from SharePoint tenant settings.")

    # If the tenant setting is already fully restricted, sites cannot be set
    # more permissively – the check passes by definition.
    restrictive = {"disabled", "existingexternalusersharingonly"}
    if tenant_cap in restrictive:
        return _result(check_id, check_name, STATUS_PASS,
                       f"SharePoint tenant sharing capability is '{tenant_cap}'; "
                       "site-level sharing cannot exceed this ceiling.")

    # Tenant allows external sharing – enumerate sites to check individual caps.
    sites = await _safe_graph_get_all(token, _SPO_SITES_URL)

    if sites is not None and sites:
        # Check whether the API returned the sharingCapability property at all.
        has_prop = any(s.get("sharingCapability") is not None for s in sites)
        if has_prop:
            permissive = [
                s.get("webUrl") or s.get("id", "unknown")
                for s in sites
                if str(s.get("sharingCapability") or "").lower()
                == "externaluserandguestsharing"
            ]
            if not permissive:
                return _result(check_id, check_name, STATUS_PASS,
                               "No SharePoint sites have 'ExternalUserAndGuestSharing' enabled.")
            preview = ", ".join(permissive[:5])
            suffix = f" (and {len(permissive) - 5} more)" if len(permissive) > 5 else ""
            return _result(check_id, check_name, STATUS_FAIL,
                           f"The following sites allow unrestricted external sharing: "
                           f"{preview}{suffix}. "
                           "Run: Get-SPOSite -Limit All | "
                           "Where-Object {$_.SharingCapability -eq 'ExternalUserAndGuestSharing'} "
                           "| Set-SPOSite -SharingCapability ExistingExternalUserSharingOnly")

    # Graph API did not return per-site sharingCapability; fall back to the
    # tenant-level assessment.  If the tenant allows guest sharing, individual
    # sites may also be configured permissively.
    return _result(check_id, check_name, STATUS_FAIL,
                   f"SharePoint tenant sharing capability is '{tenant_cap}'; "
                   "individual sites may allow unrestricted external sharing. "
                   "Run: Get-SPOSite -Limit All | Select Url,SharingCapability "
                   "to verify per-site settings, then restrict any site with "
                   "'ExternalUserAndGuestSharing' to 'ExistingExternalUserSharingOnly'.")


async def _check_onprem_password_protection(token: str) -> dict[str, Any]:
    """Check that Microsoft Entra Password Protection is configured for on-prem AD.

    Queries the Graph API organisation-level directory settings for the
    ``Password Rule Settings`` template (templateId
    ``5cf42378-d67d-4f36-ba46-e8b86229381d``) and verifies:

    * ``EnableBannedPasswordCheckOnPremises`` is ``"true"``
    * ``BannedPasswordCheckOnPremisesMode`` is ``"Enforced"`` (not ``"Audit"``)

    Note: this checks the *cloud-side configuration* only.  Whether the DC
    agent software is actually installed and running on-premises cannot be
    verified remotely via the Graph API.
    """
    check_id = "bp_onprem_password_protection"
    check_name = "Password protection is enabled for on-prem Active Directory"

    data = await _safe_graph_get(token, _ORG_SETTINGS_URL)
    if data is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to query organisation directory settings from Microsoft Graph.")

    settings_list = data.get("value") or []
    pw_setting: dict[str, Any] | None = None
    for entry in settings_list:
        if isinstance(entry, dict) and (
            entry.get("templateId") == _PASSWORD_RULE_SETTINGS_TEMPLATE_ID
            or str(entry.get("displayName") or "").lower() == "password rule settings"
        ):
            pw_setting = entry
            break

    if pw_setting is None:
        return _result(check_id, check_name, STATUS_FAIL,
                       "No 'Password Rule Settings' directory setting found. "
                       "Microsoft Entra Password Protection has not been configured for "
                       "on-premises Active Directory. "
                       "Install DC agents and configure enforcement via the Entra portal → "
                       "Protection → Authentication methods → Password protection.")

    values: dict[str, str] = {
        v["name"]: v.get("value", "")
        for v in (pw_setting.get("values") or [])
        if isinstance(v, dict) and v.get("name")
    }

    enabled_raw = str(values.get("EnableBannedPasswordCheckOnPremises", "false"))
    mode_raw = str(values.get("BannedPasswordCheckOnPremisesMode", ""))

    issues: list[str] = []
    if enabled_raw.lower() != "true":
        issues.append("EnableBannedPasswordCheckOnPremises is not set to true")
    if mode_raw.lower() != "enforced":
        issues.append(
            f"BannedPasswordCheckOnPremisesMode is '{mode_raw or 'not set'}' "
            "(should be 'Enforced')"
        )

    if not issues:
        return _result(check_id, check_name, STATUS_PASS,
                       "Microsoft Entra Password Protection is enabled and set to 'Enforced' "
                       "mode for on-premises Active Directory.")

    return _result(check_id, check_name, STATUS_FAIL,
                   "Microsoft Entra Password Protection is not fully configured for "
                   "on-premises Active Directory: " + "; ".join(issues) + ". "
                   "In the Entra portal → Protection → Authentication methods → "
                   "Password protection: enable on-prem protection and set Mode to Enforced.")


# ---------------------------------------------------------------------------
# Defender for Office 365 checks (EXO InvokeCommand)
# ---------------------------------------------------------------------------


async def _check_safe_links_office_apps(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_safe_links_office_apps"
    check_name = "Safe Links for Office applications is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-SafeLinksPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-SafeLinksPolicy: {exc}")
    rows = data.get("value") or []
    if not rows:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Safe Links policies found; create a policy that enables "
                       "Safe Links for Office applications.")
    passing = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and r.get("EnableSafeLinksForOffice") is True
        and r.get("TrackClicks") is True
        and r.get("AllowClickThrough") is False
    ]
    if passing:
        return _result(check_id, check_name, STATUS_PASS,
                       "Safe Links for Office applications is properly configured in: "
                       + ", ".join(passing[:5]))
    failing = [
        f"{r.get('Name') or r.get('Identity') or '?'} "
        f"(EnableSafeLinksForOffice={r.get('EnableSafeLinksForOffice')}, "
        f"TrackClicks={r.get('TrackClicks')}, "
        f"AllowClickThrough={r.get('AllowClickThrough')})"
        for r in rows
        if isinstance(r, dict)
    ]
    return _result(check_id, check_name, STATUS_FAIL,
                   "No Safe Links policy has EnableSafeLinksForOffice=True, "
                   "TrackClicks=True, AllowClickThrough=False. "
                   f"Policies found: {'; '.join(failing[:3])}")


async def _check_zap_teams_on(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_zap_teams_on"
    check_name = "Zero-hour auto purge for Microsoft Teams is on"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-TeamsProtectionPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-TeamsProtectionPolicy: {exc}")
    rows = data.get("value") or []
    if not rows:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Teams Protection Policy found; ZAP may not be configured.")
    zap_enabled = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict) and r.get("ZapEnabled") is True
    ]
    if zap_enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Zero-hour auto purge is enabled in: {', '.join(zap_enabled[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No Teams Protection Policy has ZapEnabled set to True. "
                   "Run: Set-TeamsProtectionPolicy -Identity 'Teams Protection Policy' -ZapEnabled $true")


# ---------------------------------------------------------------------------
# Microsoft Teams checks (EXO InvokeCommand – Teams PowerShell cmdlets)
# ---------------------------------------------------------------------------
#
# These checks call Teams PowerShell cmdlets (Get-CsTeamsMeetingPolicy,
# Get-CsTenantFederationConfiguration, Get-CsTeamsClientConfiguration) via
# the Exchange Online InvokeCommand REST endpoint.  The endpoint supports
# Teams PowerShell cmdlets alongside EXO cmdlets when the app registration
# holds the appropriate Teams admin permissions (Teams.ManageAsApp or the
# Teams Service Administrator RBAC role on the service principal).

_TEAMS_PERMISSION_HINT = (
    " The service principal requires the Teams.ManageAsApp app role "
    "and the Teams Service Administrator directory role. Re-run the "
    "'Authorize portal access' flow to grant the required permissions, "
    "or assign them manually in Microsoft Entra ID > Roles and "
    "administrators > Teams Service Administrator."
)

# Checks that call Teams PowerShell cmdlets via the Exchange Online InvokeCommand
# endpoint (Get-CsTeamsMeetingPolicy, Get-CsTenantFederationConfiguration,
# Get-CsTeamsClientConfiguration) are marked with ``"requires_teams_manage_as_app": True``
# in the catalog.  The ``Teams.ManageAsApp`` application role cannot be programmatically
# assigned to an app registration, so these checks are permanently not applicable.
_TEAMS_PS_NOT_APPLICABLE_DETAILS = (
    "Not applicable – this check calls a Teams PowerShell cmdlet via the "
    "Exchange Online InvokeCommand endpoint, which requires the "
    "Teams.ManageAsApp application role. This role cannot be programmatically "
    "assigned to an app registration and must be granted manually by a Global "
    "Administrator in Microsoft Entra ID. Until then, this check cannot be "
    "evaluated automatically."
)


def _teams_ps_error_detail(exc: M365Error, cmdlet: str) -> str:
    """Return a user-facing error detail string for a Teams PowerShell cmdlet failure.

    When the error is HTTP 403 (access denied), the message includes a
    permissions hint so administrators know which roles to grant.
    """
    if exc.http_status == 403:
        return (
            f"Unable to query {cmdlet}: access denied (403)."
            + _TEAMS_PERMISSION_HINT
        )
    return f"Unable to query {cmdlet}: {exc}"


async def _check_anon_dialin_cannot_start_meeting(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that anonymous users and dial-in callers cannot start a Teams meeting."""
    check_id = "bp_anon_dialin_cannot_start_meeting"
    check_name = "Anonymous users and dial-in callers can't start a meeting"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsMeetingPolicy", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsMeetingPolicy"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams meeting policy returned.")
    anon_start = cfg.get("AllowAnonymousUsersToStartMeeting")
    pstn_bypass = cfg.get("AllowPSTNUsersToBypassLobby")
    if anon_start is False and pstn_bypass is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "AllowAnonymousUsersToStartMeeting=False and "
                       "AllowPSTNUsersToBypassLobby=False on the Global policy.")
    issues: list[str] = []
    if anon_start is not False:
        issues.append(f"AllowAnonymousUsersToStartMeeting={anon_start}")
    if pstn_bypass is not False:
        issues.append(f"AllowPSTNUsersToBypassLobby={pstn_bypass}")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Global Teams meeting policy allows anonymous/dial-in users to start meetings: "
                   + "; ".join(issues) + ". "
                   "Run: Set-CsTeamsMeetingPolicy -Identity Global "
                   "-AllowAnonymousUsersToStartMeeting $false -AllowPSTNUsersToBypassLobby $false")


async def _check_only_org_bypass_lobby(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that only org members can bypass the Teams lobby (AutoAdmittedUsers)."""
    check_id = "bp_only_org_can_bypass_lobby"
    check_name = "Only people in my org can bypass the lobby"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsMeetingPolicy", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsMeetingPolicy"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams meeting policy returned.")
    admitted = str(cfg.get("AutoAdmittedUsers") or "").lower()
    passing_values = {"everyoneincompany", "everyoneincompanyexcludingguests"}
    if admitted in passing_values:
        return _result(check_id, check_name, STATUS_PASS,
                       f"AutoAdmittedUsers='{cfg.get('AutoAdmittedUsers')}'; only org members bypass the lobby.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"AutoAdmittedUsers='{cfg.get('AutoAdmittedUsers')}'; external participants can bypass the lobby. "
                   "Run: Set-CsTeamsMeetingPolicy -Identity Global -AutoAdmittedUsers EveryoneInCompany")


async def _check_invited_users_auto_admitted(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that only invited users are automatically admitted to Teams meetings."""
    check_id = "bp_invited_users_auto_admitted"
    check_name = "Only invited users should be automatically admitted to Teams meetings"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsMeetingPolicy", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsMeetingPolicy"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams meeting policy returned.")
    admitted = str(cfg.get("AutoAdmittedUsers") or "").lower()
    if admitted == "invitedusers":
        return _result(check_id, check_name, STATUS_PASS,
                       "AutoAdmittedUsers='InvitedUsers'; only explicitly invited participants are admitted automatically.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"AutoAdmittedUsers='{cfg.get('AutoAdmittedUsers')}'; non-invited participants may be admitted automatically. "
                   "Run: Set-CsTeamsMeetingPolicy -Identity Global -AutoAdmittedUsers InvitedUsers")


async def _check_external_participants_no_control(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that external participants cannot give or request control in Teams meetings."""
    check_id = "bp_external_participants_no_control"
    check_name = "External participants can't give or request control"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsMeetingPolicy", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsMeetingPolicy"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams meeting policy returned.")
    allow_ctrl = cfg.get("AllowExternalParticipantGiveRequestControl")
    if allow_ctrl is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "AllowExternalParticipantGiveRequestControl=False; external participants cannot give or request screen control.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"AllowExternalParticipantGiveRequestControl={allow_ctrl}; external participants can control shared screens. "
                   "Run: Set-CsTeamsMeetingPolicy -Identity Global -AllowExternalParticipantGiveRequestControl $false")


async def _check_external_users_cannot_initiate(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that external Teams users cannot initiate unsolicited conversations."""
    check_id = "bp_external_users_cannot_initiate"
    check_name = "External Teams users cannot initiate conversations"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTenantFederationConfiguration"
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTenantFederationConfiguration"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Teams federation configuration returned.")
    allow_fed = cfg.get("AllowFederatedUsers")
    if allow_fed is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "AllowFederatedUsers=False; external Teams users cannot initiate conversations.")
    # AllowFederatedUsers=True is acceptable ONLY when AllowedDomains is a
    # restricted list (not AllowAllKnownDomains).
    allowed_domains = cfg.get("AllowedDomains") or {}
    domain_type = str(
        allowed_domains.get("AllowedParent") or
        allowed_domains.get("Element") or
        allowed_domains.get("@odata.type") or ""
    ).lower()
    if "allowallknowndomains" not in domain_type and allow_fed is True:
        return _result(check_id, check_name, STATUS_PASS,
                       "AllowFederatedUsers=True but federation is restricted to a managed domain allow-list.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"AllowFederatedUsers={allow_fed} with unrestricted domain list; any external Teams tenant can contact internal users. "
                   "Run: Set-CsTenantFederationConfiguration -AllowFederatedUsers $false")


async def _check_teams_external_files_approved_storage(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that Teams only allows approved cloud storage services for file sharing."""
    check_id = "bp_teams_external_files_approved_storage"
    check_name = "External file sharing in Teams is enabled for only approved cloud storage services"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsClientConfiguration", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsClientConfiguration"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams client configuration returned.")
    third_party_props = {
        "AllowDropBox": cfg.get("AllowDropBox"),
        "AllowGoogleDrive": cfg.get("AllowGoogleDrive"),
        "AllowBox": cfg.get("AllowBox"),
        "AllowShareFile": cfg.get("AllowShareFile"),
        "AllowEgnyte": cfg.get("AllowEgnyte"),
    }
    enabled_providers = [k for k, v in third_party_props.items() if v is True]
    if not enabled_providers:
        return _result(check_id, check_name, STATUS_PASS,
                       "All third-party cloud storage providers (DropBox, GoogleDrive, Box, ShareFile, Egnyte) are disabled.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "The following third-party cloud storage providers are enabled in Teams: "
                   + ", ".join(enabled_providers) + ". "
                   "Run: Set-CsTeamsClientConfiguration -Identity Global "
                   "-AllowDropBox $false -AllowGoogleDrive $false -AllowBox $false "
                   "-AllowShareFile $false -AllowEgnyte $false")


async def _check_restrict_anon_users_join_meeting(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that anonymous users are restricted from joining Teams meetings."""
    check_id = "bp_restrict_anon_users_join_meeting"
    check_name = "Restrict anonymous users from joining meetings"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsMeetingPolicy", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsMeetingPolicy"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams meeting policy returned.")
    allow_anon = cfg.get("AllowAnonymousUsersToJoinMeeting")
    if allow_anon is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "AllowAnonymousUsersToJoinMeeting=False; unauthenticated participants cannot join Teams meetings.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"AllowAnonymousUsersToJoinMeeting={allow_anon}; anonymous users can join Teams meetings. "
                   "Run: Set-CsTeamsMeetingPolicy -Identity Global -AllowAnonymousUsersToJoinMeeting $false")


async def _check_restrict_anon_users_start_meeting(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that anonymous users cannot start Teams meetings."""
    check_id = "bp_restrict_anon_users_start_meeting"
    check_name = "Restrict anonymous users from starting Teams meetings"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-CsTeamsMeetingPolicy", {"Identity": "Global"}
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       _teams_ps_error_detail(exc, "Get-CsTeamsMeetingPolicy"))
    cfg = _exo_first_value(data)
    if not cfg:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No Global Teams meeting policy returned.")
    allow_start = cfg.get("AllowAnonymousUsersToStartMeeting")
    if allow_start is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "AllowAnonymousUsersToStartMeeting=False; anonymous users cannot start Teams meetings.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"AllowAnonymousUsersToStartMeeting={allow_start}; anonymous users can start Teams meetings without an authenticated organizer. "
                   "Run: Set-CsTeamsMeetingPolicy -Identity Global -AllowAnonymousUsersToStartMeeting $false")


# ---------------------------------------------------------------------------
# DNS checks (SPF / DMARC via DNS-over-HTTPS)
# ---------------------------------------------------------------------------


async def _check_spf_records_published(
    _token: str, email_domains: list[str]
) -> dict[str, Any]:
    check_id = "bp_spf_records_published"
    check_name = "SPF records are published for all Exchange Online domains"
    configured_domains = sorted(
        {
            str(domain).strip().lower()
            for domain in email_domains
            if str(domain).strip()
        }
    )
    if not configured_domains:
        return _result(check_id, check_name, STATUS_PASS,
                       "No Email domains are configured for this company in MyPortal; "
                       "SPF records are not required.")
    missing: list[str] = []
    errored: list[str] = []
    for domain in configured_domains:
        records = await _dns_txt_records(domain)
        if records is None:
            errored.append(domain)
            continue
        has_spf = any(r.lower().startswith("v=spf1") for r in records)
        if not has_spf:
            missing.append(domain)
    if errored and not missing:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"DNS lookup failed for {len(errored)} domain(s); "
                       "verify SPF records manually: " + ", ".join(errored[:5]))
    if not missing:
        return _result(check_id, check_name, STATUS_PASS,
                       f"SPF records found for all {len(configured_domains)} "
                       "MyPortal Email domain(s).")
    suffix = f" (DNS errors for {len(errored)} domain(s))" if errored else ""
    return _result(check_id, check_name, STATUS_FAIL,
                   f"SPF TXT record missing for {len(missing)} domain(s): "
                   + ", ".join(missing[:5]) + suffix
                   + ". Publish: v=spf1 include:spf.protection.outlook.com -all")


async def _check_dmarc_records_published(
    _token: str, email_domains: list[str]
) -> dict[str, Any]:
    check_id = "bp_dmarc_records_published"
    check_name = "DMARC records for all MyPortal Email domains are published"
    configured_domains = sorted(
        {
            str(domain).strip().lower()
            for domain in email_domains
            if str(domain).strip()
        }
    )
    if not configured_domains:
        return _result(check_id, check_name, STATUS_PASS,
                       "No Email domains are configured for this company in MyPortal; "
                       "DMARC records are not required.")
    missing: list[str] = []
    errored: list[str] = []
    for domain in configured_domains:
        records = await _dns_txt_records(f"_dmarc.{domain}")
        if records is None:
            errored.append(domain)
            continue
        has_dmarc = any(r.lower().startswith("v=dmarc1") for r in records)
        if not has_dmarc:
            missing.append(domain)
    if errored and not missing:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"DNS lookup failed for {len(errored)} domain(s); "
                       "verify DMARC records manually: " + ", ".join(errored[:5]))
    if not missing:
        return _result(
            check_id,
            check_name,
            STATUS_PASS,
            f"DMARC records found for all {len(configured_domains)} MyPortal Email domain(s).",
        )
    suffix = f" (DNS errors for {len(errored)} domain(s))" if errored else ""
    return _result(check_id, check_name, STATUS_FAIL,
                   f"DMARC TXT record missing for {len(missing)} domain(s): "
                   + ", ".join(missing[:5]) + suffix
                   + ". Publish _dmarc.<domain> TXT and use the company-specific DMARC reporting address shown in MyPortal")


# ---------------------------------------------------------------------------
# Graph-based check runners (real auto-detection)
# ---------------------------------------------------------------------------


async def _check_per_user_mfa_disabled(token: str) -> dict[str, Any]:
    check_id = "bp_per_user_mfa_disabled"
    check_name = "Per-user MFA is disabled (replaced by Conditional Access)"
    users = await _safe_graph_get_all(token, _USERS_LIST_URL)
    if users is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate users to inspect per-user MFA state.",
        )
    enabled_users: list[dict[str, str]] = []
    inspected = 0
    # Limit to a reasonable sample to avoid O(n) Graph calls on large tenants
    for user in users[:200]:
        user_id = user.get("id")
        if not user_id or not user.get("accountEnabled", True):
            continue
        url = _AUTHENTICATION_REQUIREMENTS_URL_TMPL.format(user_id=user_id)
        data = await _safe_graph_get(token, url)
        if data is None:
            continue
        inspected += 1
        state = str(data.get("perUserMfaState") or "").lower()
        if state and state != "disabled":
            enabled_users.append(_account_finding(user))
    if inspected == 0:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to determine per-user MFA state for any user (insufficient permissions?).",
        )
    if not enabled_users:
        return _result(
            check_id, check_name, STATUS_PASS,
            f"Per-user MFA is disabled across {inspected} sampled accounts.",
        )
    sample = ", ".join(account["name"] for account in enabled_users[:5])
    suffix = "" if len(enabled_users) <= 5 else f" (and {len(enabled_users) - 5} more)"
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"Per-user MFA is still enabled on {len(enabled_users)} accounts: {sample}{suffix}. "
        "Migrate these users to Conditional Access-driven MFA and disable per-user MFA.",
        enabled_users,
    )


async def _check_dynamic_group_for_guests(token: str) -> dict[str, Any]:
    check_id = "bp_dynamic_group_for_guests"
    check_name = "A dynamic group for guest users is created"
    groups = await _safe_graph_get_all(token, _GROUPS_LIST_URL)
    if groups is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate groups via Microsoft Graph.",
        )
    for grp in groups:
        types = grp.get("groupTypes") or []
        if "DynamicMembership" not in types:
            continue
        rule = (grp.get("membershipRule") or "").lower()
        if "user.usertype" in rule and "guest" in rule:
            return _result(
                check_id, check_name, STATUS_PASS,
                f"Dynamic group '{grp.get('displayName')}' targets guest users.",
            )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "No dynamic group with a membership rule targeting guest users was found.",
    )


def _ca_policy_grants_compliant_or_hybrid_joined(policy: dict[str, Any]) -> bool:
    grant = policy.get("grantControls") or {}
    controls = [str(c).lower() for c in (grant.get("builtInControls") or [])]
    return "compliantdevice" in controls or "domainjoineddevice" in controls


def _ca_policy_targets_all_users(policy: dict[str, Any]) -> bool:
    cond = policy.get("conditions") or {}
    users = cond.get("users") or {}
    include = users.get("includeUsers") or []
    return "All" in include or "all" in [str(u).lower() for u in include]


async def _check_ca_managed_device_required(token: str) -> dict[str, Any]:
    check_id = "bp_managed_device_required_auth"
    check_name = "A managed device is required for authentication"
    policies = await _safe_graph_get_all(token, _CA_POLICIES_URL)
    if policies is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate Conditional Access policies.",
        )
    for pol in policies:
        if str(pol.get("state") or "").lower() != "enabled":
            continue
        if not _ca_policy_targets_all_users(pol):
            continue
        if _ca_policy_grants_compliant_or_hybrid_joined(pol):
            return _result(
                check_id, check_name, STATUS_PASS,
                f"CA policy '{pol.get('displayName')}' requires a compliant or hybrid-joined device for sign-in.",
            )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "No enabled Conditional Access policy requires a managed/compliant device for authentication.",
    )


async def _check_ca_managed_device_for_secinfo(token: str) -> dict[str, Any]:
    check_id = "bp_managed_device_required_secinfo_reg"
    check_name = "A managed device is required to register security information"
    policies = await _safe_graph_get_all(token, _CA_POLICIES_URL)
    if policies is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate Conditional Access policies.",
        )
    for pol in policies:
        if str(pol.get("state") or "").lower() != "enabled":
            continue
        cond = pol.get("conditions") or {}
        actions = [str(a).lower() for a in ((cond.get("applications") or {}).get("includeUserActions") or [])]
        if "urn:user:registersecurityinfo" not in actions:
            continue
        if _ca_policy_grants_compliant_or_hybrid_joined(pol):
            return _result(
                check_id, check_name, STATUS_PASS,
                f"CA policy '{pol.get('displayName')}' requires a managed device to register security info.",
            )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "No enabled Conditional Access policy on the 'Register security information' user action requires a managed device.",
    )


async def _check_access_reviews_for_guests(token: str) -> dict[str, Any]:
    check_id = "bp_access_reviews_guest_users"
    check_name = "Access reviews for guest users are configured"
    defs = await _safe_graph_get_all(token, _ACCESS_REVIEWS_URL)
    if defs is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate access review definitions.",
        )
    for d in defs:
        scope = (d.get("scope") or {})
        principal_scopes = scope.get("principalScopes") or []
        for ps in principal_scopes:
            query = (ps.get("query") or "").lower()
            if "guest" in query:
                return _result(
                    check_id, check_name, STATUS_PASS,
                    f"Access review '{d.get('displayName')}' targets guest users.",
                )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "No access review definition targets guest users; create a recurring guest access review.",
    )


async def _check_access_reviews_for_privileged_roles(token: str) -> dict[str, Any]:
    check_id = "bp_access_reviews_privileged_roles"
    check_name = "Access reviews for privileged roles are configured"
    defs = await _safe_graph_get_all(token, _ACCESS_REVIEWS_URL)
    if defs is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate access review definitions.",
        )
    role_targets_found: set[str] = set()
    for d in defs:
        scope = (d.get("scope") or {})
        for ps in scope.get("principalScopes") or []:
            query = (ps.get("query") or "").lower()
            for role_id in _ADMIN_ROLE_TEMPLATES:
                if role_id.lower() in query:
                    role_targets_found.add(role_id)
    missing = _ADMIN_ROLE_TEMPLATES - role_targets_found
    if not missing:
        return _result(
            check_id, check_name, STATUS_PASS,
            "Access reviews are configured for all critical privileged roles.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"Access reviews are missing for {len(missing)} of {len(_ADMIN_ROLE_TEMPLATES)} critical privileged roles. "
        "Configure recurring reviews under Identity Governance → Access reviews.",
    )


async def _admin_user_ids(token: str) -> set[str] | None:
    """Return the set of user IDs holding an admin role in the tenant."""
    roles = await _safe_graph_get_all(token, _DIRECTORY_ROLES_URL)
    if roles is None:
        return None
    admins: set[str] = set()
    for role in roles:
        template = str(role.get("roleTemplateId") or "").lower()
        if template not in {r.lower() for r in _ADMIN_ROLE_TEMPLATES}:
            continue
        role_id = role.get("id")
        if not role_id:
            continue
        members = await _safe_graph_get_all(
            token, f"https://graph.microsoft.com/v1.0/directoryRoles/{role_id}/members"
        )
        if members is None:
            continue
        for m in members:
            uid = m.get("id")
            if uid:
                admins.add(uid)
    return admins


async def _check_admin_accounts_cloud_only(token: str) -> dict[str, Any]:
    check_id = "bp_admin_accounts_cloud_only"
    check_name = "Administrative accounts are cloud-only"
    admin_ids = await _admin_user_ids(token)
    if admin_ids is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate directory role memberships.",
        )
    if not admin_ids:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "No privileged role members found to inspect.",
        )
    synced: list[dict[str, str]] = []
    for uid in admin_ids:
        data = await _safe_graph_get(
            token,
            f"https://graph.microsoft.com/v1.0/users/{uid}"
            "?$select=userPrincipalName,onPremisesSyncEnabled",
        )
        if data and data.get("onPremisesSyncEnabled"):
            synced.append({"id": uid, "name": data.get("userPrincipalName") or uid})
    if not synced:
        return _result(
            check_id, check_name, STATUS_PASS,
            f"All {len(admin_ids)} admin accounts are cloud-only.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(synced)} admin account(s) are synced from on-premises AD: "
        + ", ".join(account["name"] for account in synced[:5]), synced,
    )


async def _check_admin_accounts_reduced_license(token: str) -> dict[str, Any]:
    check_id = "bp_admin_accounts_reduced_license"
    check_name = "Administrative accounts use licenses with reduced footprint"
    admin_ids = await _admin_user_ids(token)
    if admin_ids is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate directory role memberships.",
        )
    overlicensed: list[dict[str, str]] = []
    for uid in admin_ids:
        data = await _safe_graph_get(
            token,
            f"https://graph.microsoft.com/v1.0/users/{uid}"
            "?$select=userPrincipalName,assignedLicenses",
        )
        if not data:
            continue
        skus = data.get("assignedLicenses") or []
        # Heuristic: more than one SKU assigned to an admin is *potentially*
        # over-licensed and worth manual review.  Some admins legitimately
        # require multiple SKUs (e.g. Entra ID P2 + an O365 plan to access a
        # mailbox); the catalog remediation text and the FAIL details below
        # both make clear this is an indicative finding and admins should
        # confirm before removing licenses.
        if len(skus) > 1:
            overlicensed.append({"id": uid, "name": data.get("userPrincipalName") or uid})
    if not overlicensed:
        return _result(
            check_id, check_name, STATUS_PASS,
            "Admin accounts hold a single license SKU.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"Heuristic: {len(overlicensed)} admin account(s) hold multiple license SKUs and "
        "may be candidates for license reduction (manual verification recommended – some "
        "accounts may legitimately require multiple SKUs): "
        + ", ".join(account["name"] for account in overlicensed[:5]), overlicensed,
    )


async def _check_all_members_mfa_capable(token: str) -> dict[str, Any]:
    check_id = "bp_all_members_mfa_capable"
    check_name = "All member users are 'MFA capable'"
    rows = await _safe_graph_get_all(token, _USER_REGISTRATION_DETAILS_URL)
    if rows is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to read authentication-methods user registration details report.",
        )
    not_capable: list[dict[str, str]] = []
    for row in rows:
        if str(row.get("userType") or "").lower() != "member":
            continue
        if not row.get("isMfaCapable"):
            not_capable.append(_account_finding(row))
    if not not_capable:
        return _result(
            check_id, check_name, STATUS_PASS,
            "All member users are MFA capable.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(not_capable)} member user(s) are not MFA capable: "
        + ", ".join(account["name"] for account in not_capable[:5]), not_capable,
    )


async def _check_pim_approval_required(
    token: str, role_template_id: str, friendly_name: str, check_id: str
) -> dict[str, Any]:
    check_name = f"Approval is required for {friendly_name} role activation"
    assignments = await _safe_graph_get_all(token, _PIM_POLICIES_URL)
    if assignments is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate PIM role-management policy assignments.",
        )
    target_policy_id: str | None = None
    for a in assignments:
        if str(a.get("roleDefinitionId") or "").lower() == role_template_id.lower():
            target_policy_id = a.get("policyId")
            break
    if not target_policy_id:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            f"No PIM role-management policy assignment found for {friendly_name}.",
        )
    rules = await _safe_graph_get_all(
        token,
        f"https://graph.microsoft.com/v1.0/policies/roleManagementPolicies/{target_policy_id}/rules",
    )
    if rules is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to read PIM role-management policy rules.",
        )
    for rule in rules:
        if rule.get("id") != "Approval_EndUser_Assignment":
            continue
        setting = rule.get("setting") or {}
        if setting.get("isApprovalRequired"):
            return _result(
                check_id, check_name, STATUS_PASS,
                f"Approval is required to activate the {friendly_name} role.",
            )
        return _result(
            check_id, check_name, STATUS_FAIL,
            f"Approval is NOT required to activate the {friendly_name} role.",
        )
    return _result(
        check_id, check_name, STATUS_UNKNOWN,
        f"Could not locate the Approval_EndUser_Assignment rule for {friendly_name}.",
    )


async def _check_approval_required_ga(token: str) -> dict[str, Any]:
    return await _check_pim_approval_required(
        token, _ROLE_TEMPLATE_GLOBAL_ADMIN, "Global Administrator",
        "bp_approval_required_ga_activation",
    )


async def _check_approval_required_pra(token: str) -> dict[str, Any]:
    return await _check_pim_approval_required(
        token, _ROLE_TEMPLATE_PRIVILEGED_ROLE_ADMIN, "Privileged Role Administrator",
        "bp_approval_required_pra_activation",
    )


async def _check_collab_invitations_allowed_domains(token: str) -> dict[str, Any]:
    check_id = "bp_collab_invitations_allowed_domains"
    check_name = "Collaboration invitations are sent to allowed domains only"
    auth = await _safe_graph_get(token, _AUTHORIZATION_POLICY_URL)
    if auth is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to read authorization policy.",
        )
    invites = str(auth.get("allowInvitesFrom") or "").lower()
    if invites in {"none", "adminsandguestinviters"}:
        return _result(
            check_id, check_name, STATUS_PASS,
            f"Invitation policy is restricted to '{invites}'. Verify B2B allowed-domain list at the cross-tenant access policy.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"allowInvitesFrom is '{invites}'; restrict to 'adminsAndGuestInviters' and configure an allowed-domains list.",
    )


async def _check_custom_banned_passwords(token: str) -> dict[str, Any]:
    check_id = "bp_custom_banned_passwords"
    check_name = "Custom banned passwords lists are used"
    settings = await _safe_graph_get_all(token, _DIRECTORY_SETTINGS_URL)
    if settings is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to read directory settings.",
        )
    for s in settings:
        if (s.get("displayName") or "").lower() != "password rule settings":
            continue
        values = {v.get("name"): v.get("value") for v in s.get("values") or []}
        enable = str(values.get("EnableBannedPasswordCheck") or "").lower()
        custom_list = (values.get("BannedPasswordList") or "").strip()
        if enable == "true" and custom_list:
            return _result(
                check_id, check_name, STATUS_PASS,
                "Custom banned password list is enforced.",
            )
        return _result(
            check_id, check_name, STATUS_FAIL,
            "Password Rule Settings exist but custom banned password list is not configured.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "Password Rule Settings have not been created; custom banned passwords are not in effect.",
    )


async def _check_password_expiry_never_expire(token: str) -> dict[str, Any]:
    check_id = "bp_password_expiry_never_expire"
    check_name = "Password expiration policy is set to 'Set passwords to never expire'"
    domains = await _safe_graph_get_all(token, _DOMAINS_URL)
    if domains is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate verified domains.",
        )
    bad: list[str] = []
    for d in domains:
        if not d.get("isVerified", True):
            continue
        validity = d.get("passwordValidityPeriodInDays")
        if validity is not None and int(validity) < 2147483647:
            bad.append(f"{d.get('id')} ({validity}d)")
    if not bad:
        return _result(
            check_id, check_name, STATUS_PASS,
            "All verified domains are configured for non-expiring passwords.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(bad)} domain(s) still expire passwords: " + ", ".join(bad),
    )


async def _check_email_otp_disabled(token: str) -> dict[str, Any]:
    check_id = "bp_email_otp_disabled"
    check_name = "The email OTP authentication method is disabled"
    data = await _safe_graph_get(
        token,
        f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/Email",
    )
    if data is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to read Email authentication method configuration.",
        )
    state = str(data.get("state") or "").lower()
    if state == "disabled":
        return _result(check_id, check_name, STATUS_PASS, "Email OTP authentication method is disabled.")
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"Email OTP method state is '{state}'; should be 'disabled'.",
    )


async def _check_user_consent_disallowed(token: str) -> dict[str, Any]:
    check_id = "bp_user_consent_apps_disallowed"
    check_name = "User consent to apps accessing company data on their behalf is not allowed"
    auth = await _safe_graph_get(token, _AUTHORIZATION_POLICY_URL)
    if auth is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read authorization policy.")
    perms = (auth.get("defaultUserRolePermissions") or {}).get(
        "permissionGrantPoliciesAssigned"
    ) or []
    if perms:
        policies = ", ".join(str(p) for p in perms)
        return _result(
            check_id, check_name, STATUS_FAIL,
            f"User consent to apps is allowed via permission grant policies: {policies}.",
        )
    return _result(check_id, check_name, STATUS_PASS, "User consent to apps is not granted by default.")


async def _check_users_cannot_create_security_groups(token: str) -> dict[str, Any]:
    check_id = "bp_users_cannot_create_security_groups"
    check_name = "Users cannot create security groups"
    auth = await _safe_graph_get(token, _AUTHORIZATION_POLICY_URL)
    if auth is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read authorization policy.")
    perms = auth.get("defaultUserRolePermissions") or {}
    if perms.get("allowedToCreateSecurityGroups") is False:
        return _result(check_id, check_name, STATUS_PASS, "Users are not allowed to create security groups.")
    return _result(
        check_id, check_name, STATUS_FAIL,
        "Default user role permits creating security groups; restrict to admins only.",
    )


async def _check_users_restricted_bitlocker_recovery(token: str) -> dict[str, Any]:
    check_id = "bp_users_restricted_bitlocker_recovery"
    check_name = "Users are restricted from recovering BitLocker keys"
    auth = await _safe_graph_get(token, _AUTHORIZATION_POLICY_URL)
    if auth is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read authorization policy.")
    perms = auth.get("defaultUserRolePermissions") or {}
    if perms.get("allowedToReadBitlockerKeysForOwnedDevice") is False:
        return _result(check_id, check_name, STATUS_PASS, "Users cannot self-recover BitLocker keys.")
    return _result(
        check_id, check_name, STATUS_FAIL,
        "Default user role permits self-reading BitLocker keys; restrict to admins.",
    )


async def _check_only_managed_public_groups(token: str) -> dict[str, Any]:
    check_id = "bp_only_managed_public_groups"
    check_name = "Only organizationally managed/approved public groups exist"
    groups = await _safe_graph_get_all(token, _GROUPS_LIST_URL)
    if groups is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate groups.")
    public = [
        g for g in groups
        if str(g.get("visibility") or "").lower() == "public"
        and "Unified" in (g.get("groupTypes") or [])
    ]
    if not public:
        return _result(check_id, check_name, STATUS_PASS, "No public Microsoft 365 groups exist.")
    names = ", ".join(g.get("displayName") or "?" for g in public[:5])
    suffix = "" if len(public) <= 5 else f" (and {len(public) - 5} more)"
    affected_accounts = [_account_finding(group) for group in public]
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(public)} public Microsoft 365 group(s) exist – review and convert unapproved ones to Private: {names}{suffix}.",
        affected_accounts=affected_accounts,
    )


async def _check_pim_used(token: str) -> dict[str, Any]:
    check_id = "bp_pim_used_to_manage_roles"
    check_name = "Privileged Identity Management is used to manage roles"
    eligible = await _safe_graph_get_all(token, _PIM_ASSIGNMENTS_URL)
    if eligible is None:
        return _result(
            check_id, check_name, STATUS_UNKNOWN,
            "Unable to enumerate PIM eligible role assignments.",
        )
    if eligible:
        return _result(
            check_id, check_name, STATUS_PASS,
            f"PIM is in use ({len(eligible)} eligible role assignments).",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "No eligible (PIM-managed) role assignments exist; convert active assignments to eligible.",
    )


async def _check_phishing_resistant_mfa_admins(token: str) -> dict[str, Any]:
    check_id = "bp_phishing_resistant_mfa_admins"
    check_name = "Phishing-resistant MFA strength is required for administrators"
    policies = await _safe_graph_get_all(token, _CA_POLICIES_URL)
    if policies is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate CA policies.")
    for pol in policies:
        if str(pol.get("state") or "").lower() != "enabled":
            continue
        cond_users = (pol.get("conditions") or {}).get("users") or {}
        included_roles = [str(r).lower() for r in cond_users.get("includeRoles") or []]
        if not any(r in included_roles for r in (t.lower() for t in _ADMIN_ROLE_TEMPLATES)):
            continue
        grant = pol.get("grantControls") or {}
        strength = (grant.get("authenticationStrength") or {}).get("id") or ""
        if str(strength).lower() == _PHISHING_RESISTANT_AUTH_STRENGTH_ID:
            return _result(
                check_id, check_name, STATUS_PASS,
                f"CA policy '{pol.get('displayName')}' enforces phishing-resistant MFA for admins.",
            )
    return _result(
        check_id, check_name, STATUS_FAIL,
        "No enabled CA policy targeting admin roles requires the Phishing-Resistant MFA authentication strength.",
    )


async def _check_security_defaults_appropriate(token: str) -> dict[str, Any]:
    check_id = "bp_security_defaults_appropriate"
    check_name = "Security Defaults are appropriately configured"
    sd = await _safe_graph_get(token, _SECURITY_DEFAULTS_URL)
    if sd is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read security defaults policy.")
    policies = await _safe_graph_get_all(token, _CA_POLICIES_URL)
    has_ca = bool(
        policies and any(str(p.get("state") or "").lower() == "enabled" for p in policies)
    )
    enabled = bool(sd.get("isEnabled"))
    if has_ca and not enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       "Tenant has Conditional Access policies and Security Defaults are correctly disabled.")
    if not has_ca and enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       "Tenant lacks Conditional Access and Security Defaults are correctly enabled.")
    if has_ca and enabled:
        return _result(check_id, check_name, STATUS_FAIL,
                       "Both Conditional Access and Security Defaults are enabled; disable Security Defaults.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Tenant has neither Conditional Access nor Security Defaults; enable Security Defaults at minimum.")


def _ca_policy_targets_admin_roles(policy: dict[str, Any]) -> bool:
    cond_users = (policy.get("conditions") or {}).get("users") or {}
    included_roles = [str(r).lower() for r in cond_users.get("includeRoles") or []]
    return any(r in included_roles for r in (t.lower() for t in _ADMIN_ROLE_TEMPLATES))


async def _check_signin_freq_intune_enrollment(token: str) -> dict[str, Any]:
    check_id = "bp_signin_freq_intune_enrollment"
    check_name = "Sign-in frequency for Intune enrollment is set to 'every time'"
    policies = await _safe_graph_get_all(token, _CA_POLICIES_URL)
    if policies is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate CA policies.")
    for pol in policies:
        if str(pol.get("state") or "").lower() != "enabled":
            continue
        actions = [
            str(a).lower()
            for a in (((pol.get("conditions") or {}).get("applications") or {}).get(
                "includeUserActions"
            ) or [])
        ]
        if "urn:user:registerdevice" not in actions:
            continue
        sif = (pol.get("sessionControls") or {}).get("signInFrequency") or {}
        if str(sif.get("frequencyInterval") or "").lower() == "everytime":
            return _result(check_id, check_name, STATUS_PASS,
                           f"CA policy '{pol.get('displayName')}' enforces every-time sign-in for Intune enrollment.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No CA policy targeting the 'Register or join devices' user action enforces 'every time' sign-in.")


async def _check_signin_freq_admin_browser(token: str) -> dict[str, Any]:
    check_id = "bp_signin_freq_admin_browser_no_persist"
    check_name = "Sign-in frequency is enabled and browser sessions are not persistent for admins"
    policies = await _safe_graph_get_all(token, _CA_POLICIES_URL)
    if policies is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate CA policies.")
    for pol in policies:
        if str(pol.get("state") or "").lower() != "enabled":
            continue
        if not _ca_policy_targets_admin_roles(pol):
            continue
        sc = pol.get("sessionControls") or {}
        sif = sc.get("signInFrequency") or {}
        pb = sc.get("persistentBrowser") or {}
        sif_ok = bool(sif.get("isEnabled")) and (
            (sif.get("type") == "hours" and (sif.get("value") or 0) <= _ADMIN_SIGNIN_FREQ_MAX_HOURS)
            or sif.get("frequencyInterval") == "everyTime"
        )
        pb_ok = str(pb.get("mode") or "").lower() == "never" and pb.get("isEnabled")
        if sif_ok and pb_ok:
            return _result(check_id, check_name, STATUS_PASS,
                           f"CA policy '{pol.get('displayName')}' enforces sign-in frequency and non-persistent browser for admins.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No enabled CA policy targeting admin roles enforces both sign-in frequency and non-persistent browser sessions.")


async def _check_system_preferred_mfa(token: str) -> dict[str, Any]:
    check_id = "bp_system_preferred_mfa"
    check_name = "System-preferred multifactor authentication is enabled"
    policy = await _safe_graph_get(token, _AUTH_METHODS_POLICY_URL)
    if policy is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read authentication methods policy.")
    state = str(((policy.get("systemCredentialPreferences") or {}).get("state")) or "").lower()
    if state == "enabled":
        return _result(check_id, check_name, STATUS_PASS, "System-preferred MFA is enabled.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"System-preferred MFA state is '{state}'; should be 'enabled'.")


async def _check_authenticator_mfa_fatigue(token: str) -> dict[str, Any]:
    check_id = "bp_authenticator_mfa_fatigue"
    check_name = "Microsoft Authenticator is configured to protect against MFA fatigue"
    data = await _safe_graph_get(
        token, f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/MicrosoftAuthenticator"
    )
    if data is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read Microsoft Authenticator policy.")
    missing = _get_authenticator_mfa_fatigue_missing_settings(data)
    if not missing:
        return _result(check_id, check_name, STATUS_PASS, "All MFA-fatigue protections are enabled.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Disabled MFA-fatigue protections: " + ", ".join(missing))


def _get_authenticator_mfa_fatigue_missing_settings(data: dict[str, Any]) -> list[str]:
    """Return the Authenticator MFA-fatigue protections that are not enabled."""
    fs = data.get("featureSettings") or {}
    return [
        k for k in _MFA_FATIGUE_PROTECTION_KEYS
        if str(((fs.get(k) or {}).get("state")) or "").lower() != "enabled"
    ]


async def _check_ews_required_apps_allowed(
    graph_token: str, company_id: int
) -> dict[str, Any]:
    check_id = "bp_ews_required_apps_allowed"
    check_name = "Exchange Web Services is enabled only for confirmed required applications"
    try:
        state = await _collect_ews_dependency_state(graph_token, company_id)
    except M365Error as exc:
        return _result(
            check_id,
            check_name,
            STATUS_UNKNOWN,
            f"Unable to inspect EWS configuration and dependencies: {exc}",
        )

    current_allowed = state["current_allowed"]
    observed_apps = state["observed_apps"]
    approved_note_apps = state["approved_note_apps"]
    permission_only_apps = state["permission_only_apps"]
    missing_required_apps = state["missing_required_apps"]
    unresolved_permission_apps = state["unresolved_permission_apps"]
    usage_error = state["usage_error"]

    allowed_suffix = (
        f": {', '.join(current_allowed[:5])}"
        + ("…" if len(current_allowed) > 5 else "")
        if current_allowed
        else "."
    )
    details_parts = [
        "Current configuration: "
        f"EwsEnabled is {_format_ews_enabled(state['ews_enabled'])}; "
        f"EwsAllowedAppIDs contains {len(current_allowed)} AppID(s){allowed_suffix}"
    ]
    if observed_apps:
        details_parts.append(
            "Observed EWS usage: "
            + ", ".join(
                _format_app_label(
                    app["app_id"],
                    app.get("display_name"),
                    suffix=f"usage={app.get('usage') or '0'}"
                    + (
                        f", last seen {app['last_seen']}"
                        if app.get("last_seen")
                        else ""
                    ),
                )
                for app in observed_apps[:5]
            )
            + ("." if len(observed_apps) <= 5 else f" (and {len(observed_apps) - 5} more).")
        )
    elif usage_error:
        details_parts.append(
            "Observed EWS usage could not be confirmed because Microsoft 365 usage "
            "report data was unavailable."
        )
    else:
        details_parts.append("Observed EWS usage: none confirmed in the available usage report data.")
    if approved_note_apps:
        details_parts.append(
            "Approved from notes for infrequent or manually confirmed use: "
            + ", ".join(
                _format_app_label(app["app_id"], app.get("display_name"))
                for app in approved_note_apps[:5]
            )
            + ("." if len(approved_note_apps) <= 5 else f" (and {len(approved_note_apps) - 5} more).")
        )
    if permission_only_apps:
        details_parts.append(
            "EWS application permissions only (review before allowing): "
            + ", ".join(
                _format_app_label(app["app_id"], app.get("display_name"))
                for app in permission_only_apps[:5]
            )
            + ("." if len(permission_only_apps) <= 5 else f" (and {len(permission_only_apps) - 5} more).")
        )
    if unresolved_permission_apps:
        details_parts.append(
            "Unresolved applications with EWS-related permissions require review: "
            + ", ".join(unresolved_permission_apps[:5])
            + ("." if len(unresolved_permission_apps) <= 5 else f" (and {len(unresolved_permission_apps) - 5} more).")
        )
    if missing_required_apps:
        details_parts.append(
            "Required AppIDs missing from EwsAllowedAppIDs: "
            + ", ".join(
                _format_app_label(app["app_id"], app.get("display_name"))
                for app in missing_required_apps[:5]
            )
            + ("." if len(missing_required_apps) <= 5 else f" (and {len(missing_required_apps) - 5} more).")
        )
    if usage_error:
        details_parts.append(usage_error)
    if permission_only_apps or usage_error:
        details_parts.append(
            "Add reviewed AppIDs to the check notes if you need remediation to include infrequently used applications."
        )

    required_apps = state["required_apps"]
    if required_apps and (
        state["ews_enabled"] is not True or bool(missing_required_apps)
    ):
        status = STATUS_FAIL
    elif usage_error or unresolved_permission_apps:
        status = STATUS_UNKNOWN
    else:
        status = STATUS_PASS

    observed_ids = {app["app_id"] for app in observed_apps}
    affected_accounts = [
        {
            "id": app["app_id"],
            "name": _format_app_label(
                app["app_id"],
                app.get("display_name"),
                suffix=(
                    "observed EWS usage"
                    if app["app_id"] in observed_ids
                    else "approved in notes"
                ),
            ),
        }
        for app in missing_required_apps
    ]
    return _result(
        check_id,
        check_name,
        status,
        " ".join(details_parts),
        affected_accounts=affected_accounts or None,
    )


async def _remediate_authenticator_mfa_fatigue(token: str) -> tuple[bool, str]:
    """Apply and verify Authenticator protections despite Graph propagation lag."""
    url = (
        f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/"
        "MicrosoftAuthenticator"
    )
    current = await _safe_graph_get(token, url)
    if current is None:
        return False, "Unable to read Microsoft Authenticator policy."
    missing = _get_authenticator_mfa_fatigue_missing_settings(current)
    if missing and set(missing).issubset(_MFA_FATIGUE_MANUAL_ONLY_KEYS):
        return False, _MFA_FATIGUE_NUMBER_MATCHING_MANUAL_MESSAGE
    await _graph_patch(token, url, _MFA_FATIGUE_REMEDIATION_PAYLOAD)

    latest_details = "Microsoft Graph did not return the updated policy."
    for attempt in range(1, _MFA_FATIGUE_VERIFICATION_ATTEMPTS + 1):
        data = await _safe_graph_get(token, url)
        if data is None:
            if attempt < _MFA_FATIGUE_VERIFICATION_ATTEMPTS:
                await asyncio.sleep(_retry_backoff_seconds(attempt))
            continue
        missing = _get_authenticator_mfa_fatigue_missing_settings(data)
        if not missing:
            return True, ""
        latest_details = "Disabled MFA-fatigue protections: " + ", ".join(missing)
        if set(missing).issubset(_MFA_FATIGUE_MANUAL_ONLY_KEYS):
            return False, _MFA_FATIGUE_NUMBER_MATCHING_PARTIAL_MESSAGE
        if attempt < _MFA_FATIGUE_VERIFICATION_ATTEMPTS:
            await asyncio.sleep(_retry_backoff_seconds(attempt))

    return False, f"Microsoft Graph did not confirm the updated policy: {latest_details}"


async def _check_weak_auth_methods_disabled(token: str) -> dict[str, Any]:
    check_id = "bp_weak_auth_methods_disabled"
    check_name = "Weak authentication methods are disabled"
    issues: list[str] = []
    for method in ("Sms", "Voice", "Email"):
        data = await _safe_graph_get(
            token, f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/{method}"
        )
        if data is None:
            continue
        if str(data.get("state") or "").lower() != "disabled":
            issues.append(method)
    if not issues:
        return _result(check_id, check_name, STATUS_PASS,
                       "SMS, Voice, and Email authentication methods are disabled.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Weak methods still enabled: " + ", ".join(issues))


async def _remediate_weak_auth_methods_disabled(token: str) -> tuple[bool, str]:
    weak_methods = ("Sms", "Voice", "Email")
    for method in weak_methods:
        await _graph_patch(
            token,
            f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/{method}",
            {"state": "disabled"},
        )

    latest_details = "Microsoft Graph did not return the updated authentication method policy."
    for attempt in range(1, _WEAK_AUTH_METHODS_VERIFICATION_ATTEMPTS + 1):
        remaining: list[str] = []
        unreadable: list[str] = []
        for method in weak_methods:
            data = await _safe_graph_get(
                token,
                f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/{method}",
            )
            if data is None:
                unreadable.append(method)
                continue
            if str(data.get("state") or "").lower() != "disabled":
                remaining.append(method)

        if not remaining and not unreadable:
            return True, ""

        if remaining:
            latest_details = "Weak methods still enabled: " + ", ".join(remaining)
        else:
            latest_details = (
                "Unable to confirm weak authentication method state for: "
                + ", ".join(unreadable)
            )

        if attempt < _WEAK_AUTH_METHODS_VERIFICATION_ATTEMPTS:
            await asyncio.sleep(_retry_backoff_seconds(attempt))

    return False, (
        "Microsoft Graph did not confirm the updated weak authentication "
        f"method state: {latest_details}"
    )


def _forms_permission_guidance(action: str) -> str:
    return (
        f"The enterprise app is missing the {_FORMS_PERMISSION_NAME} application "
        f"permission required to {action} via /beta/admin/forms. Ensure tenant "
        "admin consent has been granted, then on the M365 settings page click "
        "'Authorize portal access' to re-grant the required permissions."
    )


def _parse_forms_phishing_setting(data: dict[str, Any]) -> tuple[bool | None, str | None]:
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return None, "Microsoft Graph did not return a Forms settings object."
    if "isInOrgFormsPhishingScanEnabled" not in settings:
        return (
            None,
            "Microsoft Graph did not return the isInOrgFormsPhishingScanEnabled Forms setting.",
        )
    value = settings["isInOrgFormsPhishingScanEnabled"]
    if isinstance(value, bool):
        return value, None
    return (
        None,
        "Microsoft Graph returned a non-boolean isInOrgFormsPhishingScanEnabled Forms setting.",
    )


async def _check_internal_phishing_forms(token: str) -> dict[str, Any]:
    check_id = "bp_internal_phishing_forms"
    check_name = "Internal phishing protection for Microsoft Forms is enabled"
    try:
        data = await _graph_get(token, _FORMS_ADMIN_URL)
    except M365Error as exc:
        if exc.http_status == 403:
            return _result(
                check_id,
                check_name,
                STATUS_UNKNOWN,
                _forms_permission_guidance("read Microsoft Forms settings"),
            )
        return _result(
            check_id,
            check_name,
            STATUS_UNKNOWN,
            f"Unable to query Microsoft Forms settings: {exc}",
        )

    enabled, parse_error = _parse_forms_phishing_setting(data)
    if enabled is True:
        return _result(check_id, check_name, STATUS_PASS, "Internal phishing protection for Forms is enabled.")
    if enabled is False:
        return _result(check_id, check_name, STATUS_FAIL, "Internal phishing protection for Forms is disabled.")
    return _result(
        check_id,
        check_name,
        STATUS_UNKNOWN,
        parse_error or "Unable to determine the Microsoft Forms phishing protection setting.",
    )


async def _remediate_internal_phishing_forms(token: str) -> tuple[bool, str]:
    """Enable Forms internal phishing protection and verify Graph reflects it."""
    await _graph_patch(
        token,
        _FORMS_ADMIN_URL,
        {"settings": {"isInOrgFormsPhishingScanEnabled": True}},
    )

    latest_details = ""
    for attempt in range(1, _FORMS_PHISHING_VERIFICATION_ATTEMPTS + 1):
        try:
            data = await _graph_get(token, _FORMS_ADMIN_URL)
        except M365Error as exc:
            if exc.http_status == 403:
                return False, _forms_permission_guidance("verify Microsoft Forms settings")
            latest_details = f"Unable to read Microsoft Forms settings after the update: {exc}"
        else:
            enabled, parse_error = _parse_forms_phishing_setting(data)
            if enabled is True:
                return True, ""
            if enabled is False:
                latest_details = "Internal phishing protection for Forms is disabled."
            else:
                latest_details = (
                    parse_error
                    or "Microsoft Graph did not return the updated Forms phishing protection setting."
                )
        if attempt < _FORMS_PHISHING_VERIFICATION_ATTEMPTS:
            await asyncio.sleep(_retry_backoff_seconds(attempt))

    return (
        False,
        "Microsoft Graph did not confirm the updated Forms phishing protection setting: "
        f"{latest_details}",
    )


async def _check_laps_enabled(token: str) -> dict[str, Any]:
    check_id = "bp_laps_enabled"
    check_name = "Local Administrator Password Solution (LAPS) is enabled"
    data = await _safe_graph_get(token, _DEVICE_REG_POLICY_URL)
    if data is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read device registration policy.")
    laps = (data.get("localAdminPassword") or {}).get("isEnabled")
    if laps:
        return _result(check_id, check_name, STATUS_PASS, "LAPS is enabled at the tenant level.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "LAPS is not enabled; enable it under Devices → All Devices → Device Settings → Enable Local Admin Password Solution.")


# Name used when creating (and identifying) the MyPortal-managed protection alert policy.
_BREAK_GLASS_ALERT_POLICY_NAME = "MyPortal – Break Glass Account Sign-In Alert"


async def _check_break_glass_alert_policy(scc_token: str, tenant_id: str) -> dict[str, Any]:
    """Check whether a protection alert policy exists for break-glass account sign-ins.

    Uses ``Get-ProtectionAlert`` via the Security & Compliance PowerShell REST API
    to look for the MyPortal-managed alert policy.  Returns PASS if the policy is
    present and enabled, FAIL if it is absent or disabled, and UNKNOWN if the query
    cannot be completed.

    **Licensing requirement:** The ``UserLoggedIn`` alert operation requires
    **Exchange Online Plan 2** (included in Microsoft 365 E3 and E5).
    Microsoft 365 Business Premium and lower plans include only Exchange Online Plan 1
    and do not support the ``UserLoggedIn`` audit event.  This check is automatically
    marked *Not applicable* for tenants without Exchange Online Plan 2.
    """
    check_id = "bp_break_glass_alert_policy"
    check_name = "Break-glass account sign-in alert policy is configured"
    try:
        data = await _scc_invoke_command(scc_token, tenant_id, "Get-ProtectionAlert")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query protection alert policies: {exc}")
    policies = data.get("value") or []
    for policy in policies:
        if not isinstance(policy, dict):
            continue
        if str(policy.get("Name") or "").strip() == _BREAK_GLASS_ALERT_POLICY_NAME:
            if policy.get("Disabled") is True:
                return _result(check_id, check_name, STATUS_FAIL,
                               f"Protection alert policy '{_BREAK_GLASS_ALERT_POLICY_NAME}' exists "
                               "but is disabled. Enable it to receive break-glass sign-in alerts.")
            return _result(check_id, check_name, STATUS_PASS,
                           f"Protection alert policy '{_BREAK_GLASS_ALERT_POLICY_NAME}' is present "
                           "and active. Technicians will be notified by email when a break-glass "
                           "account signs in.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"No protection alert policy named '{_BREAK_GLASS_ALERT_POLICY_NAME}' was found. "
                   "Use the automated remediation to create it, or create it manually in the "
                   "Microsoft Defender portal (Policies & rules → Alert policy).")


async def _remediate_break_glass_alert_policy(
    graph_token: str, scc_token: str, tenant_id: str
) -> tuple[bool, str]:
    """Create a protection alert policy that emails technicians when a break-glass account signs in.

    Steps:
    1. Check whether the policy already exists; if so, ensure it is enabled.
    2. Discover MyPortal-managed break-glass account UPNs from the Global Administrator role.
    3. Create ``New-ProtectionAlert`` scoped to those accounts via the SCC REST API.

    Returns ``(True, message)`` on success and ``(False, message)`` on failure.

    **Licensing requirement:** ``New-ProtectionAlert`` with ``Operation: UserLoggedIn`` requires
    **Exchange Online Plan 2** (included in Microsoft 365 E3 and E5).
    Microsoft 365 Business Premium and lower plans include only Exchange Online Plan 1
    and do not support the ``UserLoggedIn`` audit event.  This remediation will only be
    called when the tenant has been detected as holding Exchange Online Plan 2 (the catalog
    entry carries ``requires_licenses: [CAP_EXCHANGE_ONLINE_P2]``).
    """
    # 1. Check whether the policy already exists.
    try:
        existing = await _scc_invoke_command(scc_token, tenant_id, "Get-ProtectionAlert")
    except M365Error as exc:
        return False, f"Unable to query existing alert policies: {exc}"

    for policy in (existing.get("value") or []):
        if not isinstance(policy, dict):
            continue
        if str(policy.get("Name") or "").strip() == _BREAK_GLASS_ALERT_POLICY_NAME:
            if policy.get("Disabled") is True:
                # Re-enable the existing policy instead of creating a duplicate.
                try:
                    await _scc_invoke_command(
                        scc_token, tenant_id, "Set-ProtectionAlert",
                        {"Identity": _BREAK_GLASS_ALERT_POLICY_NAME, "Disabled": False},
                    )
                    return True, (
                        f"Protection alert policy '{_BREAK_GLASS_ALERT_POLICY_NAME}' was already "
                        "present but disabled – it has been re-enabled."
                    )
                except M365Error as exc:
                    return False, f"Unable to re-enable alert policy: {exc}"
            return True, (
                f"Protection alert policy '{_BREAK_GLASS_ALERT_POLICY_NAME}' already exists "
                "and is enabled; no changes were made."
            )

    # 2. Find MyPortal-managed break-glass account UPNs via Graph.
    try:
        roles = await _graph_get(
            graph_token,
            "https://graph.microsoft.com/v1.0/directoryRoles"
            "?$filter=displayName eq 'Global Administrator'&$select=id",
        )
    except M365Error as exc:
        return False, f"Unable to enumerate directory roles: {exc}"

    role_values = roles.get("value") or []
    if not role_values:
        return False, "The Global Administrator directory role is not activated in this tenant."

    try:
        members = await _graph_get_all(
            graph_token,
            f"https://graph.microsoft.com/v1.0/directoryRoles/{role_values[0]['id']}/members"
            "?$select=id,userPrincipalName,onPremisesSyncEnabled,accountEnabled",
        )
    except M365Error as exc:
        return False, f"Unable to enumerate Global Administrator members: {exc}"

    break_glass_upns = [
        str(m.get("userPrincipalName") or "")
        for m in (members or [])
        if isinstance(m, dict)
        and "myportal-emergency-admin" in str(m.get("userPrincipalName") or "").lower()
        and m.get("accountEnabled")
        and not m.get("onPremisesSyncEnabled")
    ]

    if not break_glass_upns:
        return False, (
            "No MyPortal-managed break-glass accounts (UPN containing 'myportal-emergency-admin') "
            "were found in the Global Administrator role. Run the 'Maintain 2–4 Global "
            "Administrators' remediation first to create them, then re-run this remediation."
        )

    # 3. Build the alert filter and create the policy.
    # The filter expression matches any of the discovered break-glass accounts.
    filter_parts = " OR ".join(f"User:{upn}" for upn in break_glass_upns)

    try:
        await _scc_invoke_command(
            scc_token, tenant_id, "New-ProtectionAlert",
            {
                "Name": _BREAK_GLASS_ALERT_POLICY_NAME,
                "Operation": ["UserLoggedIn"],
                "Category": "AccessGovernance",
                "Severity": "High",
                "Disabled": False,
                "Filter": filter_parts,
                "NotifyUser": ["TenantAdmins"],
                "AggregationType": "None",
                "Comment": (
                    "Created by MyPortal. Sends an email to all tenant admins whenever "
                    "a MyPortal-managed break-glass (emergency access) Global Administrator "
                    "account signs in. Review any sign-in immediately."
                ),
            },
        )
    except M365Error as exc:
        error_str = str(exc)
        # Provide a specific, actionable message when the operation is unavailable due to
        # licensing.  The UserLoggedIn alert operation requires Exchange Online Plan 2
        # (included in Microsoft 365 E3 and E5).  Microsoft 365 Business Premium and lower
        # plans include only Exchange Online Plan 1 and do not support this operation.
        if any(
            phrase in error_str.lower()
            for phrase in ("not available", "invalid operation", "unsupported", "not supported")
        ):
            return False, (
                f"Unable to create protection alert policy: {exc} – "
                "The 'UserLoggedIn' (User logged in) operation is not available on this tenant. "
                "This operation requires Exchange Online Plan 2 (included in Microsoft 365 E3 and E5). "
                "Microsoft 365 Business Premium and lower plans include only Exchange Online Plan 1 "
                "and do not support this alert operation. "
                "An upgrade to Microsoft 365 E3 or E5 is required to use this alert type."
            )
        return False, f"Unable to create protection alert policy: {exc}"

    accounts_list = ", ".join(break_glass_upns)
    return True, (
        f"Protection alert policy '{_BREAK_GLASS_ALERT_POLICY_NAME}' created successfully. "
        f"Monitoring sign-ins for: {accounts_list}. "
        "Tenant admins will receive an email alert whenever one of these accounts signs in."
    )


# ---------------------------------------------------------------------------
# Exchange Online check runners (real auto-detection)
# ---------------------------------------------------------------------------


def _exo_first_value(payload: dict[str, Any]) -> dict[str, Any]:
    val = payload.get("value")
    if isinstance(val, list) and val:
        return val[0] if isinstance(val[0], dict) else {}
    return {}


async def _check_audit_bypass_disabled_mailboxes(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_audit_bypass_disabled_mailboxes"
    check_name = "'AuditBypassEnabled' is not enabled on mailboxes"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-MailboxAuditBypassAssociation",
            {"ResultSize": "Unlimited"},
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-MailboxAuditBypassAssociation: {exc}")
    rows = data.get("value") or []
    bypassed = [
        r.get("Identity") or r.get("DisplayName") or "?"
        for r in rows
        if isinstance(r, dict) and r.get("AuditBypassEnabled") is True
    ]
    if not bypassed:
        return _result(check_id, check_name, STATUS_PASS,
                       f"No mailboxes have AuditBypassEnabled set; checked {len(rows)} associations.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(bypassed)} mailbox(es) bypass auditing: " + ", ".join(bypassed[:5]))


async def _check_audit_disabled_org_false(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_audit_disabled_org_false"
    check_name = "'AuditDisabled' organizationally is set to 'False'"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OrganizationConfig: {exc}")
    cfg = _exo_first_value(data)
    val = cfg.get("AuditDisabled")
    if val is False:
        return _result(check_id, check_name, STATUS_PASS, "Organization-level AuditDisabled is False.")
    if val is True:
        return _result(check_id, check_name, STATUS_FAIL, "Organization-level AuditDisabled is True; mailbox auditing is suppressed.")
    return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to determine AuditDisabled state.")


async def _check_audit_log_search_enabled(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_audit_log_search_enabled"
    check_name = "Microsoft 365 audit log search is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AdminAuditLogConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AdminAuditLogConfig: {exc}")
    cfg = _exo_first_value(data)
    if cfg.get("UnifiedAuditLogIngestionEnabled") is True:
        return _result(check_id, check_name, STATUS_PASS, "Unified audit log ingestion is enabled.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "UnifiedAuditLogIngestionEnabled is not True; enable audit log search in the Purview portal.")


async def _check_modern_auth_exo(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_modern_auth_exo"
    check_name = "Ensure modern authentication for Exchange Online is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OrganizationConfig: {exc}")
    cfg = _exo_first_value(data)
    if cfg.get("OAuth2ClientProfileEnabled") is True:
        return _result(check_id, check_name, STATUS_PASS, "OAuth2ClientProfileEnabled is True.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Modern authentication is disabled for Exchange Online (OAuth2ClientProfileEnabled is not True).")


async def _check_customer_lockbox(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_customer_lockbox"
    check_name = "Ensure the customer lockbox feature is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OrganizationConfig: {exc}")
    cfg = _exo_first_value(data)
    if cfg.get("CustomerLockBoxEnabled") is True:
        return _result(check_id, check_name, STATUS_PASS, "CustomerLockBoxEnabled is True.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Customer Lockbox is not enabled; enable it via "
                   "Set-OrganizationConfig -CustomerLockBoxEnabled $true.")


async def _check_organization_customization(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_organization_customization"
    check_name = "Ensure organization customization is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OrganizationConfig: {exc}")
    cfg = _exo_first_value(data)
    if cfg.get("IsDehydrated") is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "Organization customization is enabled (IsDehydrated is False).")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Organization customization is not enabled (IsDehydrated is True); "
                   "run Enable-OrganizationCustomization to enable it.")


async def _check_smtp_auth_disabled(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_smtp_auth_disabled"
    check_name = "SMTP AUTH is disabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-TransportConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-TransportConfig: {exc}")
    cfg = _exo_first_value(data)
    if cfg.get("SmtpClientAuthenticationDisabled") is True:
        return _result(check_id, check_name, STATUS_PASS, "SmtpClientAuthenticationDisabled is True.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "SMTP AUTH is enabled tenant-wide; disable via Set-TransportConfig -SmtpClientAuthenticationDisabled $true.")


async def _check_automatic_email_forwarding(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that automatic email forwarding to external recipients is system-controlled.

    Calls ``Get-RemoteDomain`` and inspects the ``AutoForwardEnabled`` property
    on the Default remote domain.  When ``AutoForwardEnabled`` is ``False`` the
    tenant blocks users from automatically forwarding email to external addresses,
    ensuring that forwarding rules are set only by administrators.

    This is the CIS Microsoft 365 Foundations Benchmark recommendation for
    "Set automatic email forwarding rules to be system controlled."
    """
    check_id = "bp_automatic_email_forwarding"
    check_name = "Automatic email forwarding to external recipients is system-controlled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-RemoteDomain")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-RemoteDomain: {exc}")
    rows = data.get("value") or []
    # Find the Default remote domain entry
    default_domain = next(
        (r for r in rows if isinstance(r, dict)
         and str(r.get("Identity") or r.get("Name") or "").lower() == "default"),
        None,
    )
    if default_domain is None:
        # Fall back to inspecting all domains if Default is not explicitly labelled
        if not rows:
            return _result(check_id, check_name, STATUS_UNKNOWN,
                           "No remote domain entries returned by Get-RemoteDomain.")
        default_domain = rows[0] if isinstance(rows[0], dict) else {}
    auto_forward = default_domain.get("AutoForwardEnabled")
    if auto_forward is False:
        return _result(check_id, check_name, STATUS_PASS,
                       "AutoForwardEnabled is False on the Default remote domain; "
                       "automatic email forwarding to external recipients is blocked.")
    if auto_forward is True:
        return _result(check_id, check_name, STATUS_FAIL,
                       "AutoForwardEnabled is True on the Default remote domain. "
                       "Users can automatically forward mail to external addresses. "
                       "Run: Set-RemoteDomain -Identity Default -AutoForwardEnabled $false")
    return _result(check_id, check_name, STATUS_UNKNOWN,
                   "Unable to determine AutoForwardEnabled state for the Default remote domain.")


async def _check_dkim_enabled_all_domains(
    exo_token: str, tenant_id: str, email_domains: list[str]
) -> dict[str, Any]:
    check_id = "bp_dkim_enabled_all_domains"
    check_name = "DKIM is enabled for all MyPortal Email domains"
    configured_domains = {
        str(domain).strip().lower() for domain in email_domains if str(domain).strip()
    }
    if not configured_domains:
        return _result(
            check_id,
            check_name,
            STATUS_NOT_APPLICABLE,
            "No Email domains are configured for this company in MyPortal.",
        )
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-DkimSigningConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-DkimSigningConfig: {exc}")
    rows = data.get("value") or []
    configs = {
        str(r.get("Domain") or r.get("Identity") or "").strip().lower(): r
        for r in rows
        if isinstance(r, dict) and (r.get("Domain") or r.get("Identity"))
    }
    disabled = sorted(
        domain
        for domain in configured_domains
        if domain not in configs or configs[domain].get("Enabled") is not True
    )
    if not disabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"DKIM is enabled for all {len(configured_domains)} MyPortal Email domains.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"DKIM is disabled or unavailable on {len(disabled)} MyPortal Email domain(s): "
                   + ", ".join(disabled[:5]))


async def _check_third_party_storage_owa(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_third_party_storage_owa"
    check_name = "Additional storage providers are restricted in Outlook on the Web"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OwaMailboxPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OwaMailboxPolicy: {exc}")
    rows = data.get("value") or []
    bad = [
        r.get("Identity") or r.get("Name") or "?"
        for r in rows
        if isinstance(r, dict) and r.get("AdditionalStorageProvidersAvailable") is True
    ]
    if not bad:
        return _result(check_id, check_name, STATUS_PASS,
                       "Additional storage providers are restricted in all OWA mailbox policies.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"OWA policies allowing third-party storage: " + ", ".join(bad))


async def _check_outlook_addins_disabled(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_outlook_addins_disabled"
    check_name = "Users installing Outlook add-ins is not allowed"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OwaMailboxPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OwaMailboxPolicy: {exc}")
    rows = data.get("value") or []
    bad = [
        r.get("Identity") or r.get("Name") or "?"
        for r in rows
        if isinstance(r, dict) and r.get("WebPartsFrameworkEnabled") is True
    ]
    if not bad:
        return _result(check_id, check_name, STATUS_PASS,
                       "User Outlook add-in installation is disabled in all OWA mailbox policies.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "OWA policies allowing user Outlook add-in installation: " + ", ".join(bad))


async def _check_idle_session_timeout(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_idle_session_timeout_3h"
    check_name = "Idle session timeout is 3 hours or less for unmanaged devices"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OrganizationConfig: {exc}")
    cfg = _exo_first_value(data)
    enabled = cfg.get("ActivityBasedAuthenticationTimeoutEnabled")
    interval = str(cfg.get("ActivityBasedAuthenticationTimeoutInterval") or "")
    # Format hh:mm:ss – compare hours
    try:
        hours = int(interval.split(":")[0]) if interval else 99
    except ValueError:
        hours = 99
    if enabled is True and hours <= 3:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Idle session timeout enabled at {interval}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"Idle session timeout enabled={enabled}, interval={interval or 'unset'}; set ≤ 03:00:00.")


async def _check_mailtips_enabled(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_mailtips_enabled"
    check_name = "MailTips are enabled for end users"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-OrganizationConfig: {exc}")
    cfg = _exo_first_value(data)
    if cfg.get("MailTipsAllTipsEnabled") is True:
        return _result(check_id, check_name, STATUS_PASS,
                       "MailTipsAllTipsEnabled is True; MailTips are enabled for end users.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "MailTipsAllTipsEnabled is not True; MailTips are not fully enabled for end users.")


async def _check_shared_mailbox_signin_blocked(token: str) -> dict[str, Any]:
    """Identify shared-mailbox user accounts that have not been disabled.

    Uses Microsoft Graph (not EXO) – Graph exposes a stable shape and the
    tenant has Directory.Read.All from existing best-practices grants.
    """
    check_id = "bp_shared_mailbox_signin_blocked"
    check_name = "Sign-in to shared mailboxes is blocked"
    # ``mailboxSettings`` does not expose the SharedMailbox flag via Graph; the
    # closest portable signal is `userType=Member` users with no licenses
    # whose accountEnabled is True – combined with the fact that admin-portal
    # shared mailboxes always lack a license. We surface this as a heuristic
    # check; admins with EXO PowerShell can confirm via Get-Mailbox.
    users = await _safe_graph_get_all(token, _USERS_LIST_URL)
    if users is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate users.")
    admin_ids = await _get_directory_role_member_ids(token)
    if admin_ids is None:
        return _result(
            check_id,
            check_name,
            STATUS_UNKNOWN,
            "Unable to enumerate administrator role members; no accounts were evaluated.",
        )
    candidates = [
        u for u in users
        if (u.get("userType") or "").lower() == "member"
        and not (u.get("assignedLicenses") or [])
        and u.get("accountEnabled") is True
        and str(u.get("id") or "") not in admin_ids
    ]
    if not candidates:
        return _result(check_id, check_name, STATUS_PASS,
                       "No non-admin unlicensed member accounts are sign-in enabled (likely no shared mailbox is sign-in enabled).")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(candidates)} non-admin unlicensed member account(s) appear to be sign-in enabled (likely shared mailboxes). "
                   "Disable each via Update-MgUser -UserId <id> -AccountEnabled:$false. "
                   "First sample: " + ", ".join((u.get("userPrincipalName") or u.get("id") or "?") for u in candidates[:5]),
                   [_account_finding(user) for user in candidates])


async def _check_mailbox_audit_actions(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_mailbox_audit_actions"
    check_name = "Mailbox audit actions are configured"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-Mailbox",
            {"ResultSize": 100, "Filter": "RecipientTypeDetails -eq 'UserMailbox'"},
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-Mailbox: {exc}")
    rows = data.get("value") or []
    bad: list[dict[str, str]] = []
    required_owner = {"MailboxLogin", "HardDelete", "SoftDelete", "Update"}
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("AuditEnabled") is not True:
            bad.append(_account_finding(r, identity_key="UserPrincipalName"))
            continue
        owner = set(r.get("AuditOwner") or [])
        if not required_owner.issubset(owner):
            bad.append(_account_finding(r, identity_key="UserPrincipalName"))
    if not bad:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Audit actions properly configured on {len(rows)} sampled mailboxes.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(bad)} mailbox(es) lack the recommended audit actions: " + ", ".join(a["name"] for a in bad[:5]), bad)


async def _check_antiphish_impersonated_domain_protection(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_impersonated_domain_protection"
    check_name = "Anti-phishing impersonated domain protection is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    enabled = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and _coerce_exo_bool(r.get("EnableTargetedDomainsProtection"))
    ]
    if enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Impersonated domain protection is enabled in: {', '.join(enabled[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has EnableTargetedDomainsProtection set to True. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-EnableTargetedDomainsProtection $true")


async def _check_antiphish_impersonated_user_protection(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_impersonated_user_protection"
    check_name = "Anti-phishing impersonated user protection is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    enabled = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and _coerce_exo_bool(r.get("EnableTargetedUserProtection"))
    ]
    if enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Impersonated user protection is enabled in: {', '.join(enabled[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has EnableTargetedUserProtection set to True. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-EnableTargetedUserProtection $true")


async def _check_antiphish_quarantine_impersonated_domain(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_quarantine_impersonated_domain"
    check_name = "Messages from impersonated domains are quarantined"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    quarantine = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and str(r.get("TargetedDomainProtectionAction") or "").lower() == "quarantine"
    ]
    if quarantine:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Impersonated-domain messages are quarantined in: {', '.join(quarantine[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has TargetedDomainProtectionAction set to Quarantine. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-TargetedDomainProtectionAction Quarantine")


async def _check_antiphish_quarantine_impersonated_user(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_quarantine_impersonated_user"
    check_name = "Messages from impersonated users are quarantined"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    quarantine = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and str(r.get("TargetedUserProtectionAction") or "").lower() == "quarantine"
    ]
    if quarantine:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Impersonated-user messages are quarantined in: {', '.join(quarantine[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has TargetedUserProtectionAction set to Quarantine. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-TargetedUserProtectionAction Quarantine")


async def _check_antiphish_domain_impersonation_safety_tip(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_domain_impersonation_safety_tip"
    check_name = "Domain impersonation safety tip is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    enabled = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and _coerce_exo_bool(r.get("EnableSimilarDomainsSafetyTips"))
    ]
    if enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Domain impersonation safety tip is enabled in: {', '.join(enabled[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has EnableSimilarDomainsSafetyTips set to True. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-EnableSimilarDomainsSafetyTips $true")


async def _check_antiphish_user_impersonation_safety_tip(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_user_impersonation_safety_tip"
    check_name = "User impersonation safety tip is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    enabled = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and _coerce_exo_bool(r.get("EnableSimilarUsersSafetyTips"))
    ]
    if enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"User impersonation safety tip is enabled in: {', '.join(enabled[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has EnableSimilarUsersSafetyTips set to True. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-EnableSimilarUsersSafetyTips $true")


async def _check_antiphish_unusual_characters_safety_tip(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_antiphish_unusual_characters_safety_tip"
    check_name = "User impersonation unusual characters safety tip is enabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-AntiPhishPolicy: {exc}")
    rows = data.get("value") or []
    enabled = [
        r.get("Name") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict)
        and _coerce_exo_bool(r.get("EnableUnusualCharactersSafetyTips"))
    ]
    if enabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Unusual characters safety tip is enabled in: {', '.join(enabled[:5])}.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "No anti-phishing policy has EnableUnusualCharactersSafetyTips set to True. "
                   "Run: Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
                   "-EnableUnusualCharactersSafetyTips $true")


async def _check_mailbox_auditing_enabled_all_users(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_mailbox_auditing_enabled"
    check_name = "Ensure mailbox auditing for all users is Enabled"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-Mailbox",
            {"ResultSize": "Unlimited", "Filter": "RecipientTypeDetails -eq 'UserMailbox'"},
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-Mailbox: {exc}")
    rows = data.get("value") or []
    if not rows:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No user mailboxes found to evaluate.")
    not_audited = [
        _account_finding(r, identity_key="UserPrincipalName")
        for r in rows
        if isinstance(r, dict) and r.get("AuditEnabled") is not True
    ]
    if not not_audited:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Mailbox auditing (AuditEnabled) is enabled on all {len(rows)} user mailbox(es).")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(not_audited)} user mailbox(es) do not have AuditEnabled set to True: "
                   + ", ".join(a["name"] for a in not_audited[:5])
                   + ("…" if len(not_audited) > 5 else ""), not_audited)


async def _check_block_users_message_limit(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that outbound spam filter policies block users who reach the message limit.

    Calls ``Get-HostedOutboundSpamFilterPolicy`` and inspects every policy for
    the ``ActionWhenThresholdReached`` property.  The recommended action is
    ``BlockUser`` so that accounts that exceed the outbound sending limit are
    immediately blocked from sending further mail, reducing the blast radius of
    a compromised account used for spam.
    """
    check_id = "bp_block_users_message_limit"
    check_name = "Block users who reached the message limit"
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-HostedOutboundSpamFilterPolicy"
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-HostedOutboundSpamFilterPolicy: {exc}")
    rows = data.get("value") or []
    if not rows:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No outbound spam filter policies returned.")
    failing: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("ActionWhenThresholdReached") or "").strip()
        name = row.get("Name") or row.get("Identity") or "Default"
        if action.lower() != "blockuser":
            failing.append(f"{name} (ActionWhenThresholdReached={action!r}; should be 'BlockUser')")
    if not failing:
        return _result(check_id, check_name, STATUS_PASS,
                       f"All {len(rows)} outbound spam filter "
                       f"{'policy' if len(rows) == 1 else 'policies'} block users "
                       "when the message limit is reached.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(failing)} {'policy does' if len(failing) == 1 else 'policies do'} "
                   "not block users when the message limit is reached: "
                   + "; ".join(failing[:5]))


async def _check_quarantine_notification_enabled(
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    """Check that end-user spam/quarantine notifications are enabled with a daily frequency.

    Quarantine notifications are now controlled by the global quarantine
    policy.  The similarly named hosted content filter policy properties are
    deprecated and Exchange Online rejects attempts to update them.
    """
    check_id = "bp_quarantine_notification_enabled"
    check_name = "End-user spam quarantine notifications are enabled with a daily frequency"
    try:
        data = await _exo_invoke_command(
            exo_token,
            tenant_id,
            "Get-QuarantinePolicy",
        )
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query the global quarantine policy: {exc}")
    rows = data.get("value") or []
    if not rows:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No global quarantine policy was returned.")
    policy = _select_global_quarantine_policy(rows)
    if not policy:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No global quarantine policy was returned.")
    if policy.get("ESNEnabled") is not True:
        return _result(check_id, check_name, STATUS_FAIL,
                       "The global quarantine policy has notifications disabled.")
    frequency = str(policy.get("EndUserSpamNotificationFrequency") or "").strip()
    if frequency not in {"1", "1.00:00:00", "24:00:00"}:
        return _result(check_id, check_name, STATUS_FAIL,
                       f"The global quarantine notification frequency is {frequency!r}; "
                       "it should be one day.")
    return _result(check_id, check_name, STATUS_PASS,
                   "The global quarantine policy has notifications enabled with a daily frequency.")


def _select_global_quarantine_policy(rows: list[Any]) -> dict[str, Any] | None:
    """Select the best global quarantine policy row from Get-QuarantinePolicy results."""
    policies = [row for row in rows if isinstance(row, dict)]
    if not policies:
        return None

    typed_matches = [
        row for row in policies
        if str(row.get("QuarantinePolicyType") or "").strip().lower() == "globalquarantinepolicy"
    ]
    if typed_matches:
        built_in = next((row for row in typed_matches if row.get("IsBuiltInPolicy") is True), None)
        return built_in or typed_matches[0]

    name_matches = [
        row for row in policies
        if str(row.get("Identity") or row.get("Name") or "").strip().lower() == "globalquarantinepolicy"
    ]
    if name_matches:
        return name_matches[0]

    return None


_BEST_PRACTICES: list[dict[str, Any]] = [
    {
        "id": "bp_security_defaults",
        "name": "Enable Security Defaults",
        "description": (
            "Microsoft recommends Security Defaults as the baseline identity "
            "security configuration for tenants without Conditional Access."
        ),
        "remediation": (
            "Enable Security Defaults: Azure portal → Azure Active Directory → "
            "Properties → Manage security defaults → Enable."
        ),
        "source": _check_security_defaults,
        "default_enabled": True,
        "has_remediation": False,
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_block_legacy_auth",
        "name": "Block legacy authentication",
        "description": (
            "Block legacy authentication protocols (POP, IMAP, SMTP basic auth) "
            "to prevent password-spray and credential-stuffing attacks."
        ),
        "remediation": (
            "Create a Conditional Access policy that targets all users and "
            "blocks 'Other clients' / 'Exchange ActiveSync clients'."
        ),
        "source": _check_legacy_auth_blocked,
        "default_enabled": True,
        "has_remediation": False,
        "is_cis_benchmark": True,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_mfa_for_all_users",
        "name": "Require MFA for all users",
        "description": (
            "Microsoft recommends multi-factor authentication for every user "
            "to defend against compromised credentials."
        ),
        "remediation": (
            "Create a Conditional Access policy assigned to All users that "
            "requires multi-factor authentication under Grant controls."
        ),
        "source": _check_mfa_conditional_access,
        "default_enabled": True,
        "has_remediation": False,
        "is_cis_benchmark": True,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_admin_mfa",
        "name": "Require MFA for administrators",
        "description": (
            "Privileged accounts must always require strong authentication."
        ),
        "remediation": (
            "Ensure all admin role holders are registered for MFA and have it "
            "enforced via Conditional Access or per-user MFA."
        ),
        "source": _check_admin_mfa,
        "default_enabled": True,
        "has_remediation": False,
        "is_cis_benchmark": True,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_global_admin_count",
        "name": "Maintain 2–4 Global Administrators",
        "description": (
            "Maintain between two and four Global Administrators, aiming for three, "
            "to balance availability and minimise blast radius."
        ),
        "remediation": (
            "Create enough emergency Global Administrator accounts to reach the target of three "
            "and store each generated credential as a separate Hudu password."
        ),
        "source": _check_global_admin_count,
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "global_admin_accounts",
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_audit_log_enabled",
        "name": "Enable unified audit log",
        "description": (
            "The unified audit log captures activity across Microsoft 365 "
            "workloads and is required for incident investigations."
        ),
        "remediation": (
            "Enable in the Compliance portal: Audit → Start recording user "
            "and admin activity."
        ),
        "source": _check_audit_log_enabled,
        "default_enabled": True,
        "has_remediation": False,
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_self_service_password_reset",
        "name": "Enable Self-Service Password Reset",
        "description": (
            "SSPR reduces help-desk load and improves user experience while "
            "maintaining strong identity hygiene."
        ),
        "remediation": (
            "Azure AD → Password reset → Properties → Self-service password "
            "reset enabled = All."
        ),
        "source": _check_sspr_enabled,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _AUTHORIZATION_POLICY_URL,
        "remediation_payload": {
            "defaultUserRolePermissions": {
                "allowedToUseSspr": True,
            },
        },
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_password_never_expires",
        "name": "Disable password expiration",
        "description": (
            "Microsoft and NIST recommend not expiring passwords when MFA is "
            "in place; forced rotations weaken password quality."
        ),
        "remediation": (
            "Microsoft 365 admin center → Settings → Org Settings → Security "
            "& privacy → Password expiration policy → Set passwords to never "
            "expire."
        ),
        "source": _check_password_never_expires,
        "default_enabled": True,
        "has_remediation": False,
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_guest_access_restricted",
        "name": "Restrict guest user access",
        "description": (
            "Limit what external guest accounts can see and do in your "
            "directory to reduce data-exposure risk."
        ),
        "remediation": (
            "Azure AD → External Identities → External collaboration "
            "settings → Guest user access restrictions → Restricted."
        ),
        "source": _check_guest_access_restricted,
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _AUTHORIZATION_POLICY_URL,
        "remediation_payload": {
            "guestUserRoleId": _GUEST_ROLE_ID_MOST_RESTRICTIVE,
            "allowInvitesFrom": "adminsAndGuestInviters",
        },
        "is_cis_benchmark": True,
    },
    # ------------------------------------------------------------------
    # Exchange Online checks
    # ------------------------------------------------------------------
    {
        "id": "bp_disable_direct_send",
        "name": "Disable Direct Send",
        "description": (
            "Direct Send allows external senders to relay mail through your "
            "Exchange Online tenant without authentication. Disabling it "
            "prevents unauthorized mail relay and reduces spam/phishing risk."
        ),
        "remediation": (
            "Run the following Exchange Online PowerShell command to disable "
            "Direct Send: Set-OrganizationConfig -RejectDirectSend $true"
        ),
        "source": _check_direct_send,
        "source_type": "exo",
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {"RejectDirectSend": True},
        "default_enabled": True,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_it_contact_baseline",
        "name": "Configure IT contact forwarding baseline",
        "description": (
            "Provide IT and IT Support contacts in the GAL that forward to the "
            "separate MSP addresses configured globally for MyPortal."
        ),
        "remediation": (
            "Create the two hidden mail contacts and distribution groups, plus "
            "the external-forward transport rule. Existing objects with different "
            "values must be corrected manually and are never overwritten."
        ),
        "source": _check_it_contact_baseline,
        "source_type": "exo",
        "has_remediation": True,
        "remediation_type": "it_contact_baseline_exo",
        "default_enabled": True,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    # ------------------------------------------------------------------
    # Monitoring best practices
    # ------------------------------------------------------------------
    {
        "id": "bp_monitor_sign_in_logs",
        "name": "Sign-in audit logs accessible",
        "description": (
            "Sign-in logs are essential for incident investigation and "
            "monitoring potentially compromised accounts."
        ),
        "remediation": (
            "Ensure the Microsoft Graph sign-in logs API is accessible: grant "
            "AuditLog.Read.All and verify the tenant has an Azure AD Premium "
            "P1 or P2 license."
        ),
        "source": _check_monitor_sign_in_logs,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_monitor_risky_users",
        "name": "No active high-risk users",
        "description": (
            "Microsoft Entra ID Protection flags users whose credentials may "
            "be compromised; risky users should be investigated promptly."
        ),
        "remediation": (
            "Investigate and remediate risky users in the Entra portal → "
            "Protection → Identity Protection → Risky users."
        ),
        "source": _check_monitor_risky_users,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_monitor_sign_in_risk_policy",
        "name": "Sign-in risk policy enabled",
        "description": (
            "Enabling the sign-in risk policy automatically challenges or "
            "blocks risky sign-ins detected by Entra ID Protection."
        ),
        "remediation": (
            "Entra portal → Protection → Identity Protection → Sign-in risk "
            "policy → assign to All users, set risk level to Medium and above, "
            "and require MFA."
        ),
        "source": _check_monitor_sign_in_risk_policy,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_monitor_user_risk_policy",
        "name": "User risk policy enabled",
        "description": (
            "The user risk policy automatically blocks or requires a password "
            "reset for accounts considered compromised."
        ),
        "remediation": (
            "Entra portal → Protection → Identity Protection → User risk "
            "policy → assign to All users, set risk level to High, and "
            "require secure password change."
        ),
        "source": _check_monitor_user_risk_policy,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_monitor_named_locations",
        "name": "Named locations configured for Conditional Access",
        "description": (
            "Defining trusted named locations enables Conditional Access "
            "policies to use location as a strong signal."
        ),
        "remediation": (
            "Entra portal → Protection → Conditional Access → Named "
            "locations → add trusted IP ranges or country lists."
        ),
        "source": _check_monitor_named_locations,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_monitor_ca_report_only_policies",
        "name": "No core security CA policies stuck in report-only",
        "description": (
            "Conditional Access policies in report-only mode provide no "
            "protection; security-critical policies should be fully enabled."
        ),
        "remediation": (
            "Entra portal → Protection → Conditional Access → policy → set "
            "state to 'On' after reviewing the report-only insights."
        ),
        "source": _check_monitor_ca_report_only_policies,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_monitor_app_credential_expiry",
        "name": "No app registration credentials expiring within 30 days",
        "description": (
            "Expired application secrets cause authentication failures and "
            "service outages; rotate credentials proactively."
        ),
        "remediation": (
            "Entra portal → App registrations → application → Certificates & "
            "secrets → create a new secret/certificate and update dependent "
            "services before the existing one expires."
        ),
        "source": _check_myportal_pkce_app_credential_expiry,
        "default_enabled": True,
        "default_auto_remediate": True,
        "has_remediation": True,
        "uses_company_id": True,
        "remediation_type": "renew_myportal_pkce_admin_secret",
    },
    {
        "id": "bp_monitor_cloud_admin_accounts",
        "name": "Privileged accounts are cloud-only (not hybrid-synced)",
        "description": (
            "Microsoft strongly recommends that Global Administrator accounts "
            "are cloud-only identities to prevent on-premises compromise from "
            "escalating to the cloud."
        ),
        "remediation": (
            "Create dedicated cloud admin accounts in Entra ID and remove the "
            "Global Administrator role from any synced accounts."
        ),
        "source": _check_monitor_cloud_admin_accounts,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_secure_score",
        "name": "Microsoft Secure Score is at or above 50%",
        "description": (
            "Microsoft Secure Score is the primary KPI for overall M365 "
            "security posture; tracking it ensures continuous improvement."
        ),
        "remediation": (
            "Microsoft 365 Defender portal → Secure Score → review and "
            "implement recommended improvement actions."
        ),
        "source": _check_monitor_secure_score,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_mfa_registration_policy",
        "name": "Authentication methods policy is configured",
        "description": (
            "Microsoft recommends explicitly configuring the authentication "
            "methods policy with modern methods such as Microsoft "
            "Authenticator and FIDO2 keys."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Policies → "
            "enable Microsoft Authenticator, FIDO2 security keys, and other "
            "modern methods."
        ),
        "source": _check_monitor_mfa_registration_policy,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_concealed_names",
        "name": "Concealed user, group, and site names in all reports is disabled",
        "description": (
            "Microsoft 365 usage reports should display real user, group, and site "
            "names so that administrators can accurately audit activity and identify "
            "issues.  When the 'Display concealed names' setting is enabled, obfuscated "
            "identifiers are shown instead, which reduces the usefulness of usage reports."
        ),
        "remediation": (
            "Run the PowerShell command: "
            "Update-MgAdminReportSetting -DisplayConcealedNames $false\n"
            "Or via the Microsoft 365 admin center: Settings → Org settings → "
            "Services → Reports → disable 'Display concealed user, group, and site names'."
        ),
        "source": _check_concealed_names,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _REPORT_SETTINGS_URL,
        "remediation_payload": {"displayConcealedNames": False},
    },
    # ------------------------------------------------------------------
    # Identity & Conditional Access (Microsoft Graph)
    # ------------------------------------------------------------------
    {
        "id": "bp_per_user_mfa_disabled",
        "name": "'Per-user MFA' is disabled",
        "description": (
            "Per-user MFA is the legacy way of enforcing MFA. Microsoft recommends "
            "migrating users to Conditional Access-driven MFA and disabling per-user MFA."
        ),
        "remediation": (
            "For each affected user run: "
            "Update-MgBetaUserAuthenticationRequirement -UserId <upn> "
            "-PerUserMfaState Disabled. Ensure a Conditional Access policy "
            "requiring MFA is in place first."
        ),
        "source": _check_per_user_mfa_disabled,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "disable_per_user_mfa",
    },
    {
        "id": "bp_dynamic_group_for_guests",
        "name": "A dynamic group for guest users is created",
        "description": (
            "A dynamic Entra ID group whose membership rule targets guest users "
            "lets administrators easily scope access reviews and Conditional "
            "Access policies to all guests."
        ),
        "remediation": (
            "Entra portal → Groups → New group → Group type: Security, "
            "Membership type: Dynamic User, "
            "Dynamic query: (user.userType -eq \"Guest\")."
        ),
        "source": _check_dynamic_group_for_guests,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "create_dynamic_guest_group",
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_managed_device_required_auth",
        "name": "A managed device is required for authentication",
        "description": (
            "Conditional Access should require a compliant or hybrid Azure AD "
            "joined device for all sign-ins to ensure only managed endpoints "
            "can access corporate resources."
        ),
        "remediation": (
            "Entra portal → Protection → Conditional Access → New policy → "
            "Users: All users → Cloud apps: All cloud apps → "
            "Grant: Require device to be marked as compliant OR Require "
            "Hybrid Azure AD joined device."
        ),
        "source": _check_ca_managed_device_required,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_managed_device_required_secinfo_reg",
        "name": "A managed device is required to register security information",
        "description": (
            "Restricting security info registration to managed devices prevents "
            "attackers who phish credentials from registering their own MFA method."
        ),
        "remediation": (
            "Conditional Access → New policy → Cloud apps → User actions → "
            "'Register security information' → Grant: Require compliant device."
        ),
        "source": _check_ca_managed_device_for_secinfo,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_access_reviews_guest_users",
        "name": "Access reviews for guest users are configured",
        "description": (
            "Recurring access reviews of guest accounts ensure stale guests "
            "are removed promptly, reducing data-exposure risk."
        ),
        "remediation": (
            "Entra portal → Identity Governance → Access reviews → New access "
            "review → Users: Guest users only → recurrence: quarterly."
        ),
        "source": _check_access_reviews_for_guests,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_access_reviews_privileged_roles",
        "name": "Access reviews for privileged roles are configured",
        "description": (
            "Recurring access reviews of admins (GA, PRA, SA, Exchange Admin, "
            "Billing Admin) prevent role accumulation and unauthorised retention."
        ),
        "remediation": (
            "Entra portal → Identity Governance → Privileged Identity Management "
            "→ Roles → for each privileged role click 'Access reviews' → New."
        ),
        "source": _check_access_reviews_for_privileged_roles,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_admin_accounts_cloud_only",
        "name": "Administrative accounts are cloud-only",
        "description": (
            "Privileged accounts must not be synced from on-premises AD so that "
            "an on-premises compromise cannot escalate to the cloud."
        ),
        "remediation": (
            "Create dedicated cloud-only admin accounts in Entra ID and remove "
            "privileged role assignments from any synced accounts."
        ),
        "source": _check_admin_accounts_cloud_only,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_admin_accounts_reduced_license",
        "name": "Administrative accounts use licenses with reduced footprint",
        "description": (
            "Admin accounts should only carry the minimum licensing required "
            "(typically Entra ID P1/P2) to reduce attack surface and cost."
        ),
        "remediation": (
            "Microsoft 365 admin center → Users → select admin → Licenses → "
            "remove all but the minimum required license."
        ),
        "source": _check_admin_accounts_reduced_license,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_all_members_mfa_capable",
        "name": "All member users are 'MFA capable'",
        "description": (
            "A user is MFA-capable when they are licensed for and registered for "
            "at least one strong authentication method. Drive registration to 100%."
        ),
        "remediation": (
            "Use the Authentication methods activity report to identify users "
            "without a registered method, then drive registration via "
            "MyAccount → Security info → Add method."
        ),
        "source": _check_all_members_mfa_capable,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_approval_required_ga_activation",
        "name": "Approval is required for Global Administrator role activation",
        "description": (
            "Requiring approval for GA activation in PIM ensures a second "
            "person reviews every privilege escalation."
        ),
        "remediation": (
            "Entra portal → Identity Governance → PIM → Microsoft Entra roles "
            "→ Settings → Global Administrator → Edit → Activation → Require approval to activate."
        ),
        "source": _check_approval_required_ga,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_approval_required_pra_activation",
        "name": "Approval is required for Privileged Role Administrator activation",
        "description": (
            "Requiring approval for PRA activation prevents a single compromised "
            "privileged role administrator from granting roles unilaterally."
        ),
        "remediation": (
            "Entra portal → PIM → Microsoft Entra roles → Settings → Privileged "
            "Role Administrator → Edit → Require approval to activate."
        ),
        "source": _check_approval_required_pra,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_collab_invitations_allowed_domains",
        "name": "Collaboration invitations are sent to allowed domains only",
        "description": (
            "Restricting B2B invitations to a curated allow-list of partner "
            "domains prevents accidental collaboration with unknown organisations."
        ),
        "remediation": (
            "Entra portal → External Identities → Cross-tenant access settings "
            "→ Default settings → B2B collaboration → Allow specific domains."
        ),
        "source": _check_collab_invitations_allowed_domains,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _AUTHORIZATION_POLICY_URL,
        "remediation_payload": {"allowInvitesFrom": "adminsAndGuestInviters"},
    },
    {
        "id": "bp_custom_banned_passwords",
        "name": "Custom banned passwords lists are used",
        "description": (
            "A custom banned-password list (company-name, products, etc.) "
            "prevents users from selecting predictable passwords."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Password "
            "protection → set 'Enforce custom list' to Yes and add company-specific terms."
        ),
        "source": _check_custom_banned_passwords,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_password_expiry_never_expire",
        "name": "Password expiration policy is set to 'Set passwords to never expire'",
        "description": (
            "When MFA is in place, NIST/Microsoft recommend not expiring "
            "passwords. Forced rotations weaken password quality."
        ),
        "remediation": (
            "Microsoft 365 admin center → Settings → Org settings → Security "
            "& privacy → Password expiration policy → Set passwords to never expire."
        ),
        "source": _check_password_expiry_never_expire,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_email_otp_disabled",
        "name": "The email OTP authentication method is disabled",
        "description": (
            "Email OTP is a weak authentication method that should be disabled "
            "in favour of phishing-resistant or push-based methods."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Policies → "
            "Email OTP → Enable: No, Target: All users."
        ),
        "source": _check_email_otp_disabled,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": (
            f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/Email"
        ),
        "remediation_payload": {"state": "disabled"},
    },
    {
        "id": "bp_user_consent_apps_disallowed",
        "name": "User consent to apps accessing company data on their behalf is not allowed",
        "description": (
            "Allowing arbitrary user consent to OAuth apps is a primary vector "
            "for illicit consent attacks. Restrict consent to admins."
        ),
        "remediation": (
            "Entra portal → Enterprise applications → Consent and permissions "
            "→ User consent settings → Do not allow user consent."
        ),
        "source": _check_user_consent_disallowed,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "is_cis_benchmark": True,
        "remediation_url": _AUTHORIZATION_POLICY_URL,
        "remediation_payload": {
            "defaultUserRolePermissions": {"permissionGrantPoliciesAssigned": []}
        },
    },
    {
        "id": "bp_users_cannot_create_security_groups",
        "name": "Users cannot create security groups",
        "description": (
            "Allowing arbitrary group creation makes group sprawl and "
            "unintended permission grants more likely."
        ),
        "remediation": (
            "Entra portal → Groups → General → Users can create security groups → No."
        ),
        "source": _check_users_cannot_create_security_groups,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _AUTHORIZATION_POLICY_URL,
        "remediation_payload": {
            "defaultUserRolePermissions": {"allowedToCreateSecurityGroups": False}
        },
    },
    {
        "id": "bp_users_restricted_bitlocker_recovery",
        "name": "Users are restricted from recovering BitLocker keys",
        "description": (
            "Allowing users to retrieve BitLocker recovery keys from MyAccount "
            "creates an attack path for a phished account to decrypt a stolen device."
        ),
        "remediation": (
            "Entra portal → Devices → Device settings → Restrict users from "
            "recovering BitLocker key(s) for their owned devices → Yes."
        ),
        "source": _check_users_restricted_bitlocker_recovery,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _AUTHORIZATION_POLICY_URL,
        "remediation_payload": {
            "defaultUserRolePermissions": {
                "allowedToReadBitlockerKeysForOwnedDevice": False
            }
        },
        "requires_licenses": [CAP_INTUNE],
    },
    {
        "id": "bp_only_managed_public_groups",
        "name": "Only organisationally managed/approved public groups exist",
        "description": (
            "Public Microsoft 365 groups expose conversations and files to all "
            "tenant users; convert unapproved groups to Private."
        ),
        "remediation": (
            "Entra portal → Groups → All groups → for each public group click "
            "Properties → Privacy: Private."
        ),
        "source": _check_only_managed_public_groups,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "foreach_public_group_graph",
    },
    {
        "id": "bp_pim_used_to_manage_roles",
        "name": "Privileged Identity Management is used to manage roles",
        "description": (
            "Standing privileged role assignments should be converted to "
            "eligible PIM assignments so admins must explicitly activate roles."
        ),
        "remediation": (
            "Entra portal → PIM → Microsoft Entra roles → Roles → for each "
            "active assignment, choose 'Make eligible' and require activation."
        ),
        "source": _check_pim_used,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P2],
    },
    {
        "id": "bp_phishing_resistant_mfa_admins",
        "name": "Phishing-resistant MFA strength is required for administrators",
        "description": (
            "Administrators must authenticate with phishing-resistant methods "
            "(FIDO2 keys, Windows Hello for Business, certificate-based) to "
            "defeat AiTM phishing kits."
        ),
        "remediation": (
            "Conditional Access → New policy → Users: include privileged "
            "directory roles → Cloud apps: All cloud apps → "
            "Grant: Require authentication strength → Phishing-resistant MFA."
        ),
        "source": _check_phishing_resistant_mfa_admins,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_security_defaults_appropriate",
        "name": "Security Defaults are appropriately configured",
        "description": (
            "Security Defaults should be enabled on tenants without "
            "Conditional Access, and disabled when Conditional Access is in use "
            "to avoid duplicate enforcement."
        ),
        "remediation": (
            "Entra portal → Properties → Manage security defaults → toggle "
            "based on whether Conditional Access policies are in place."
        ),
        "source": _check_security_defaults_appropriate,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_signin_freq_intune_enrollment",
        "name": "Sign-in frequency for Intune enrollment is set to 'every time'",
        "description": (
            "Requiring re-authentication every time a device enrolls into "
            "Intune prevents stale tokens from being abused for device join."
        ),
        "remediation": (
            "Conditional Access → New policy → Cloud apps → User actions → "
            "'Register or join devices' → Session → Sign-in frequency: Every time."
        ),
        "source": _check_signin_freq_intune_enrollment,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1, CAP_INTUNE],
    },
    {
        "id": "bp_signin_freq_admin_browser_no_persist",
        "name": "Sign-in frequency is enabled and browser sessions are not persistent for admins",
        "description": (
            "Limit administrator browser sessions to a few hours and disable "
            "persistent browser sessions to reduce token-theft impact."
        ),
        "remediation": (
            "Conditional Access → New policy → Users: privileged roles → "
            "Session → Sign-in frequency: 4 hours, Persistent browser: Never persistent."
        ),
        "source": _check_signin_freq_admin_browser,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    {
        "id": "bp_system_preferred_mfa",
        "name": "System-preferred multifactor authentication is enabled",
        "description": (
            "System-preferred MFA prompts users with their strongest registered "
            "method first, reducing the use of weaker methods."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Settings → "
            "System-preferred multifactor authentication → Enabled."
        ),
        "source": _check_system_preferred_mfa,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _AUTH_METHODS_POLICY_URL,
        "remediation_payload": {
            "systemCredentialPreferences": {"state": "enabled"}
        },
    },
    {
        "id": "bp_authenticator_mfa_fatigue",
        "name": "Microsoft Authenticator is configured to protect against MFA fatigue",
        "description": (
            "Number matching, app context and location context defeat MFA "
            "fatigue and consent-spam attacks against Microsoft Authenticator."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Microsoft "
            "Authenticator → Configure → enable Number matching, Show app "
            "name and Show location for all users."
        ),
        "source": _check_authenticator_mfa_fatigue,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": (
            f"{_AUTH_METHODS_POLICY_URL}/authenticationMethodConfigurations/"
            "MicrosoftAuthenticator"
        ),
        "remediation_payload": _MFA_FATIGUE_REMEDIATION_PAYLOAD,
    },
    {
        "id": "bp_weak_auth_methods_disabled",
        "name": "Weak authentication methods are disabled",
        "description": (
            "SMS, Voice and Email OTP are vulnerable to SIM-swapping and "
            "phishing; disable in favour of Microsoft Authenticator and FIDO2."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Policies → "
            "for SMS, Voice, Email OTP → Enable: No."
        ),
        "source": _check_weak_auth_methods_disabled,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
    },
    {
        "id": "bp_internal_phishing_forms",
        "name": "Internal phishing protection for Microsoft Forms is enabled",
        "description": (
            "Forms can include keyword-based phishing protection that warns "
            "users when a form attempts to harvest credentials."
        ),
        "remediation": (
            "Microsoft 365 admin center → Settings → Org settings → Microsoft "
            "Forms → Phishing protection → Add internal phishing protection."
        ),
        "source": _check_internal_phishing_forms,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _FORMS_ADMIN_URL,
        "remediation_payload": {"settings": {"isInOrgFormsPhishingScanEnabled": True}},
    },
    {
        "id": "bp_laps_enabled",
        "name": "Local Administrator Password Solution (LAPS) is enabled",
        "description": (
            "LAPS rotates each managed Windows device's local administrator "
            "password and stores it securely in Entra ID/Intune."
        ),
        "remediation": (
            "Entra portal → Devices → All devices → Device settings → Enable "
            "Microsoft Entra Local Administrator Password Solution (LAPS): Yes. "
            "Then create an Intune Account Protection policy from the LAPS template."
        ),
        "source": _check_laps_enabled,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1, CAP_INTUNE_LAPS],
    },
    {
        "id": "bp_break_glass_alert_policy",
        "name": "Break-glass account sign-in alert policy is configured",
        "description": (
            "An alert policy should be in place so that technicians are notified by email "
            "whenever a break-glass (emergency access) Global Administrator account signs in. "
            "Unexpected use of these accounts may indicate a security incident. "
            "Alert policies are evaluated against the unified audit log via the Microsoft Defender portal "
            "and require Exchange Online with the unified audit log enabled "
            "(see 'Enable unified audit log' and 'UnifiedAuditLogIngestionEnabled' checks). "
            "The 'User logged in' (UserLoggedIn) operation used by this policy relies on "
            "Exchange Online mailbox audit logging, which requires Exchange Online Plan 2 "
            "(included in Microsoft 365 E3 and E5). "
            "Microsoft 365 Business Premium and lower plans include only Exchange Online Plan 1 "
            "and do not support this alert operation."
        ),
        "remediation": (
            "In the Microsoft Defender portal go to Policies & rules → Alert policy → New alert policy. "
            "Set Operation to 'User logged in' (requires Exchange Online Plan 2, included in Microsoft 365 E3/E5), "
            "Category to 'Access governance', Severity to 'High', "
            "scope the policy to your break-glass account UPNs, and add your on-call technicians "
            "as notification recipients. Alternatively use the automated remediation to create the "
            "policy automatically for MyPortal-managed break-glass accounts. "
            "Note: Microsoft 365 Business Premium and lower plans include only Exchange Online Plan 1 "
            "and do not support the 'User logged in' operation; an upgrade to Microsoft 365 E3 or E5 "
            "is required to use this alert type."
        ),
        "source": _check_break_glass_alert_policy,
        "source_type": "scc",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "break_glass_alert_policy",
        "requires_licenses": [CAP_EXCHANGE_ONLINE_P2],
    },
    # ------------------------------------------------------------------
    # Exchange Online (real auto-detection via EXO REST)
    # ------------------------------------------------------------------
    {
        "id": "bp_audit_bypass_disabled_mailboxes",
        "name": "'AuditBypassEnabled' is not enabled on mailboxes",
        "description": (
            "Mailboxes with AuditBypassEnabled bypass the unified audit log, "
            "leaving no trace of suspicious activity."
        ),
        "remediation": (
            "For each affected mailbox: "
            "Set-MailboxAuditBypassAssociation -Identity <upn> -AuditBypassEnabled $false"
        ),
        "source": _check_audit_bypass_disabled_mailboxes,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_audit_disabled_org_false",
        "name": "'AuditDisabled' organizationally is set to 'False'",
        "description": (
            "When OrganizationConfig.AuditDisabled is True, mailbox auditing "
            "is suppressed for every mailbox in the tenant."
        ),
        "remediation": "Set-OrganizationConfig -AuditDisabled $false",
        "source": _check_audit_disabled_org_false,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {"AuditDisabled": False},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_audit_log_search_enabled",
        "name": "Microsoft 365 audit log search is enabled",
        "description": (
            "The unified audit log is the primary source for incident "
            "investigation; ingestion must be enabled for events to be searchable."
        ),
        "remediation": "Set-AdminAuditLogConfig -UnifiedAuditLogIngestionEnabled $true",
        "source": _check_audit_log_search_enabled,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-AdminAuditLogConfig",
        "remediation_params": {"UnifiedAuditLogIngestionEnabled": True},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_mailbox_audit_actions",
        "name": "Mailbox audit actions are configured",
        "description": (
            "Per-mailbox audit actions (MailboxLogin, HardDelete, SendAs, …) "
            "should be configured so audit log records contain rich context."
        ),
        "remediation": (
            "For each mailbox: Set-Mailbox -Identity <upn> -AuditEnabled $true "
            "-AuditOwner @{Add='MailboxLogin','HardDelete','SoftDelete','Update'}"
        ),
        "source": _check_mailbox_audit_actions,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "foreach_mailbox_exo",
        "remediation_mailbox_params": {
            "AuditEnabled": True,
            "AuditOwner": {
                "Add": ["MailboxLogin", "HardDelete", "SoftDelete", "Update"]
            },
        },
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_mailbox_auditing_enabled",
        "name": "Ensure mailbox auditing for all users is Enabled",
        "description": (
            "Mailbox audit logging records actions taken on each mailbox by "
            "mailbox owners, delegates, and admins. Enabling AuditEnabled on "
            "every user mailbox ensures that activity is captured in the unified "
            "audit log for forensic investigation and compliance purposes."
        ),
        "remediation": (
            "Enable auditing on all user mailboxes via Exchange Online PowerShell: "
            "Get-Mailbox -RecipientTypeDetails UserMailbox -ResultSize Unlimited "
            "| Set-Mailbox -AuditEnabled $true"
        ),
        "source": _check_mailbox_auditing_enabled_all_users,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "foreach_mailbox_exo",
        "remediation_mailbox_params": {"AuditEnabled": True},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_block_users_message_limit",
        "name": "Block users who reached the message limit",
        "description": (
            "When a user exceeds the outbound message sending limit, Exchange Online "
            "can alert administrators, restrict the user, or block the account from "
            "sending mail entirely. Setting ActionWhenThresholdReached to BlockUser "
            "ensures that a compromised account being used for spam is immediately "
            "prevented from sending further messages, limiting the blast radius and "
            "protecting the tenant's sending reputation."
        ),
        "remediation": (
            "For each outbound spam filter policy:\n"
            "Set-HostedOutboundSpamFilterPolicy -Identity <name> "
            "-ActionWhenThresholdReached BlockUser"
        ),
        "source": _check_block_users_message_limit,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-HostedOutboundSpamFilterPolicy",
        "remediation_params": {"ActionWhenThresholdReached": "BlockUser"},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_modern_auth_exo",
        "name": "Ensure modern authentication for Exchange Online is enabled",
        "description": (
            "Modern authentication (OAuth2) enables token-based authentication "
            "and multi-factor authentication for Exchange Online clients. Without "
            "it, Outlook clients fall back to basic authentication which cannot "
            "be protected by Conditional Access policies or MFA, leaving "
            "credentials vulnerable to interception and password-spray attacks."
        ),
        "remediation": "Set-OrganizationConfig -OAuth2ClientProfileEnabled $true",
        "source": _check_modern_auth_exo,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {"OAuth2ClientProfileEnabled": True},
        "is_cis_benchmark": True,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_ews_required_apps_allowed",
        "name": "Exchange Web Services is enabled only for confirmed required applications",
        "description": (
            "EWS retirement requires tenants that still depend on Exchange Web "
            "Services to explicitly enable EWS and restrict access to approved "
            "application IDs only."
        ),
        "remediation": (
            "Review observed EWS usage, add any infrequently used but approved "
            "AppIDs to the check notes, then enable EWS with "
            "Set-OrganizationConfig -EwsEnabled $true -EwsAllowedAppIDs "
            "<existing + approved app IDs>."
        ),
        "source": _check_ews_required_apps_allowed,
        "source_type": "graph",
        "uses_company_id": True,
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "ews_dependency_allow_list",
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_customer_lockbox",
        "name": "Ensure the customer lockbox feature is enabled",
        "description": (
            "Customer Lockbox ensures that Microsoft cannot access customer "
            "content to perform a service operation without explicit customer "
            "approval. Enabling it provides an additional layer of control and "
            "transparency, allowing organisations to review, approve, or reject "
            "Microsoft engineer access requests to their data."
        ),
        "remediation": (
            "Set-OrganizationConfig -CustomerLockBoxEnabled $true "
            "(run manually as a Global Administrator in Exchange Online PowerShell)"
        ),
        "source": _check_customer_lockbox,
        "source_type": "exo",
        "default_enabled": True,
        # CustomerLockBoxEnabled is protected by an interactive administrator
        # authorization check.  App-only Exchange.ManageAsApp tokens receive
        # 403 even when their service principal has Exchange or Compliance
        # Administrator, so exposing automated remediation is misleading.  Do
        # not grant the integration app Global Administrator to bypass this
        # safeguard; direct an administrator to the manual command instead.
        "has_remediation": False,
        "is_cis_benchmark": True,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_organization_customization",
        "name": "Ensure organization customization is enabled",
        "description": (
            "Exchange Online tenants start in a dehydrated (uncustomised) state to "
            "reduce resource usage. Many Exchange Online and Security & Compliance "
            "cmdlets — including transport rules, journaling, data loss prevention, "
            "and custom retention policies — require organisation customisation to be "
            "enabled before they can be configured. Running Enable-OrganizationCustomization "
            "is a prerequisite for applying security and compliance controls to the tenant."
        ),
        "remediation": "Enable-OrganizationCustomization",
        "source": _check_organization_customization,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Enable-OrganizationCustomization",
        "remediation_params": {},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_smtp_auth_disabled",
        "name": "SMTP AUTH is disabled",
        "description": (
            "SMTP basic-auth submission is a primary vector for password-spray "
            "and credential-stuffing; disable it tenant-wide."
        ),
        "remediation": "Set-TransportConfig -SmtpClientAuthenticationDisabled $true",
        "source": _check_smtp_auth_disabled,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-TransportConfig",
        "remediation_params": {"SmtpClientAuthenticationDisabled": True},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_automatic_email_forwarding",
        "name": "Automatic email forwarding to external recipients is system-controlled",
        "description": (
            "When AutoForwardEnabled is True on the Default remote domain, users "
            "can configure inbox rules or mailbox settings to silently forward all "
            "email to an external address. This is a common data-exfiltration "
            "technique used by attackers after gaining access to a mailbox. "
            "Disabling automatic forwarding at the transport layer ensures that "
            "only administrators can establish legitimate forwarding, giving the "
            "organisation full control over outbound mail flow."
        ),
        "remediation": (
            "Disable automatic external forwarding for the Default remote domain:\n"
            "Set-RemoteDomain -Identity Default -AutoForwardEnabled $false"
        ),
        "source": _check_automatic_email_forwarding,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-RemoteDomain",
        "remediation_params": {"Identity": "Default", "AutoForwardEnabled": False},
        "is_cis_benchmark": True,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_dkim_enabled_all_domains",
        "name": "DKIM is enabled for all MyPortal Email domains",
        "description": (
            "DKIM signs outbound mail with a tenant-controlled key, allowing "
            "recipients to verify authenticity and reject spoofed messages."
        ),
        "remediation": (
            "Publish the two CNAME records reported by Get-DkimSigningConfig "
            "at your DNS registrar, then run "
            "Set-DkimSigningConfig -Identity <domain> -Enabled $true."
        ),
        "source": _check_dkim_enabled_all_domains,
        "source_type": "exo",
        "uses_company_email_domains": True,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_third_party_storage_owa",
        "name": "Additional storage providers are restricted in Outlook on the Web",
        "description": (
            "Disabling third-party cloud storage providers in OWA prevents "
            "accidental data exfiltration to consumer storage services."
        ),
        "remediation": (
            "For each OWA mailbox policy: "
            "Set-OwaMailboxPolicy -Identity <name> -AdditionalStorageProvidersAvailable $false"
        ),
        "source": _check_third_party_storage_owa,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "foreach_owa_mailbox_policy_exo",
        "remediation_cmdlet": "Set-OwaMailboxPolicy",
        "remediation_params": {"AdditionalStorageProvidersAvailable": False},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_outlook_addins_disabled",
        "name": "Users installing Outlook add-ins is not allowed",
        "description": (
            "Preventing users from installing Outlook add-ins reduces the risk "
            "of malicious or data-exfiltrating add-ins being installed without "
            "administrative oversight."
        ),
        "remediation": (
            "For each OWA mailbox policy: "
            "Set-OwaMailboxPolicy -Identity <name> -WebPartsFrameworkEnabled $false"
        ),
        "source": _check_outlook_addins_disabled,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-OwaMailboxPolicy",
        "remediation_params": {"WebPartsFrameworkEnabled": False},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_idle_session_timeout_3h",
        "name": "Idle session timeout is set to 3 hours or less for unmanaged devices",
        "description": (
            "Activity-based authentication timeout reduces session-hijack "
            "exposure on unmanaged or shared devices."
        ),
        "remediation": (
            "Set-OrganizationConfig -ActivityBasedAuthenticationTimeoutEnabled $true "
            "-ActivityBasedAuthenticationTimeoutInterval 03:00:00"
        ),
        "source": _check_idle_session_timeout,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {
            "ActivityBasedAuthenticationTimeoutEnabled": True,
            "ActivityBasedAuthenticationTimeoutInterval": "03:00:00",
        },
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_mailtips_enabled",
        "name": "MailTips are enabled for end users",
        "description": (
            "MailTips warn users about potential issues before they send an email "
            "(e.g. replying-all to large groups, sending to external recipients, or "
            "sending to restricted distribution lists), helping to prevent data leaks "
            "and accidental mis-sends."
        ),
        "remediation": "Set-OrganizationConfig -MailTipsAllTipsEnabled $true",
        "source": _check_mailtips_enabled,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {"MailTipsAllTipsEnabled": True},
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_shared_mailbox_signin_blocked",
        "name": "Sign-in to shared mailboxes is blocked",
        "description": (
            "Shared mailboxes should be sign-in disabled so attackers cannot "
            "log in to them directly even if they obtain credentials. Unlicensed "
            "accounts assigned to administrator roles are excluded."
        ),
        "remediation": (
            "For each shared mailbox: "
            "Update-MgUser -UserId <upn> -AccountEnabled:$false"
        ),
        "source": _check_shared_mailbox_signin_blocked,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "foreach_user_graph",
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_antiphish_impersonated_domain_protection",
        "name": "Anti-phishing impersonated domain protection is enabled",
        "description": (
            "Enabling targeted domain protection in anti-phishing policies "
            "allows Microsoft Defender to identify and act on messages that "
            "spoof domains you own or that you have added to the protected "
            "domains list."
        ),
        "remediation": (
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-EnableTargetedDomainsProtection $true "
            "-TargetedDomainsToProtect @('<yourdomain.com>')"
        ),
        "source": _check_antiphish_impersonated_domain_protection,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_antiphish_impersonated_user_protection",
        "name": "Anti-phishing impersonated user protection is enabled",
        "description": (
            "Enabling targeted user protection in anti-phishing policies "
            "allows Microsoft Defender to identify and act on messages that "
            "impersonate specific high-value users such as executives."
        ),
        "remediation": (
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-EnableTargetedUserProtection $true "
            "-TargetedUsersToProtect @('<user@yourdomain.com>')"
        ),
        "source": _check_antiphish_impersonated_user_protection,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_antiphish_quarantine_impersonated_domain",
        "name": "Messages from impersonated domains are quarantined",
        "description": (
            "When targeted domain protection is active, the detection action "
            "should be set to Quarantine so impersonation attempts are "
            "isolated rather than merely flagged."
        ),
        "remediation": (
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-TargetedDomainProtectionAction Quarantine"
        ),
        "source": _check_antiphish_quarantine_impersonated_domain,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-AntiPhishPolicy",
        "remediation_params": {
            "Identity": "Office365 AntiPhish Default",
            "TargetedDomainProtectionAction": "Quarantine",
            "Confirm": False,
        },
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_antiphish_quarantine_impersonated_user",
        "name": "Messages from impersonated users are quarantined",
        "description": (
            "When targeted user protection is active, the detection action "
            "should be set to Quarantine so impersonation attempts are "
            "isolated rather than merely flagged."
        ),
        "remediation": (
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-TargetedUserProtectionAction Quarantine"
        ),
        "source": _check_antiphish_quarantine_impersonated_user,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-AntiPhishPolicy",
        "remediation_params": {
            "Identity": "Office365 AntiPhish Default",
            "TargetedUserProtectionAction": "Quarantine",
            "Confirm": False,
        },
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_antiphish_domain_impersonation_safety_tip",
        "name": "Domain impersonation safety tip is enabled",
        "description": (
            "Enabling the domain impersonation safety tip shows users a warning "
            "banner when a message appears to come from a domain that looks similar "
            "to a protected domain, helping users identify spoofed senders."
        ),
        "remediation": (
            "For each anti-phishing policy with domain impersonation protection enabled:\n"
            "Set-AntiPhishPolicy -Identity <name> "
            "-EnableSimilarDomainsSafetyTips $true"
        ),
        "source": _check_antiphish_domain_impersonation_safety_tip,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "matching_antiphish_policy_exo",
        "remediation_cmdlet": "Set-AntiPhishPolicy",
        "remediation_params": {
            "EnableSimilarDomainsSafetyTips": True,
            "Confirm": False,
        },
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_antiphish_user_impersonation_safety_tip",
        "name": "User impersonation safety tip is enabled",
        "description": (
            "Enabling the user impersonation safety tip displays a warning to "
            "recipients when a message appears to come from a user that looks "
            "similar to a protected user, reducing the risk of impersonation attacks."
        ),
        "remediation": (
            "For each anti-phishing policy with user impersonation protection enabled:\n"
            "Set-AntiPhishPolicy -Identity <name> "
            "-EnableSimilarUsersSafetyTips $true"
        ),
        "source": _check_antiphish_user_impersonation_safety_tip,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "matching_antiphish_policy_exo",
        "remediation_cmdlet": "Set-AntiPhishPolicy",
        "remediation_params": {
            "EnableSimilarUsersSafetyTips": True,
            "Confirm": False,
        },
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_antiphish_unusual_characters_safety_tip",
        "name": "User impersonation unusual characters safety tip is enabled",
        "description": (
            "Enabling the unusual characters safety tip alerts users when a "
            "message contains unusual character sets in the sender address, "
            "a common tactic used in look-alike domain impersonation attacks."
        ),
        "remediation": (
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-EnableUnusualCharactersSafetyTips $true"
        ),
        "source": _check_antiphish_unusual_characters_safety_tip,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-AntiPhishPolicy",
        "remediation_params": {
            "Identity": "Office365 AntiPhish Default",
            "EnableUnusualCharactersSafetyTips": True,
            "Confirm": False,
        },
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_quarantine_notification_enabled",
        "name": "End-user spam quarantine notifications are enabled with a daily frequency",
        "description": (
            "Ensuring end-user quarantine notifications are enabled and set to the "
            "shortest available interval (1 day) means users are alerted promptly "
            "when legitimate mail is quarantined, reducing the risk of missed "
            "communications. Exchange Online supports 1-, 2-, or 3-day notification "
            "intervals; 1 day is the best available approximation of a 4-hour "
            "notification window."
        ),
        "remediation": (
            "Set-QuarantinePolicy -Identity GlobalQuarantinePolicy "
            "-ESNEnabled $true -EndUserSpamNotificationFrequency 1.00:00:00"
        ),
        "source": _check_quarantine_notification_enabled,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_type": "global_quarantine_policy_exo",
        "remediation_cmdlet": "Set-QuarantinePolicy",
        "remediation_params": {
            "ESNEnabled": True,
            "EndUserSpamNotificationFrequency": "1.00:00:00",
        },
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    # ------------------------------------------------------------------
    # SharePoint Online / OneDrive (manual-review pending SPO PowerShell client)
    # ------------------------------------------------------------------
    {
        "id": "bp_external_content_sharing_restricted",
        "name": "External content sharing is restricted",
        "description": (
            "Limiting external sharing to existing external users only prevents "
            "accidental sharing with anonymous parties."
        ),
        "remediation": (
            "Connect-SPOService -Url https://<tenant>-admin.sharepoint.com\n"
            "Set-SPOTenant -SharingCapability ExistingExternalUserSharingOnly"
        ),
        "source": _check_external_content_sharing_restricted,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
    },
    {
        "id": "bp_sharepoint_external_sharing_restricted",
        "name": "SharePoint external sharing is restricted",
        "description": (
            "Per-site sharing capability should match or be more restrictive "
            "than the tenant-wide setting."
        ),
        "remediation": (
            "Get-SPOSite -Limit All | Where-Object {$_.SharingCapability -eq 'ExternalUserAndGuestSharing'} "
            "| Set-SPOSite -SharingCapability ExistingExternalUserSharingOnly"
        ),
        "source": _check_sharepoint_external_sharing_restricted,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _SPO_SETTINGS_URL,
        "remediation_payload": {"sharingCapability": "existingExternalUserSharingOnly"},
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
    },
    {
        "id": "bp_sp_guests_cannot_share_unowned",
        "name": "SharePoint guest users cannot share items they don't own",
        "description": (
            "External users should not be permitted to re-share items they do "
            "not own, preventing data sprawl."
        ),
        "remediation": "Set-SPOTenant -PreventExternalUsersFromResharing $true",
        "source": _check_sp_guests_cannot_share_unowned,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _SPO_SETTINGS_URL,
        "remediation_payload": {"isResharingByExternalUsersEnabled": False},
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
    },
    {
        "id": "bp_onedrive_content_sharing_restricted",
        "name": "OneDrive content sharing is restricted",
        "description": (
            "OneDrive sharing should be limited to existing external users to "
            "match the SharePoint posture."
        ),
        "remediation": "Set-SPOTenant -OneDriveSharingCapability ExistingExternalUserSharingOnly",
        "source": _check_onedrive_content_sharing_restricted,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _SPO_SETTINGS_URL,
        "remediation_payload": {"oneDriveSharingCapability": "existingExternalUserSharingOnly"},
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
    },
    {
        "id": "bp_link_sharing_restricted_spo_od",
        "name": "Link sharing is restricted in SharePoint and OneDrive",
        "description": (
            "Default to 'Specific people' links with View-only permission to "
            "minimise accidental over-sharing."
        ),
        "remediation": "Set-SPOTenant -DefaultSharingLinkType Direct -DefaultLinkPermission View",
        "source": _check_link_sharing_restricted_spo_od,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
    },
    {
        "id": "bp_modern_auth_sp_apps",
        "name": "Modern authentication for SharePoint applications is required",
        "description": (
            "Disabling legacy auth protocols on SharePoint Online prevents "
            "older clients from bypassing Conditional Access."
        ),
        "remediation": "Set-SPOTenant -LegacyAuthProtocolsEnabled $false",
        "source": _check_modern_auth_sp_apps,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _SPO_SETTINGS_URL,
        "remediation_payload": {"isLegacyAuthProtocolsEnabled": False},
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
    },
    {
        "id": "bp_sharepoint_infected_files_block",
        "name": "Office 365 SharePoint infected files are disallowed for download",
        "description": (
            "Blocking download of infected files from SharePoint/OneDrive "
            "prevents Defender-detected malware from spreading further."
        ),
        "remediation": "Set-SPOTenant -DisallowInfectedFileDownload $true",
        "source": _check_sharepoint_infected_files_block,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_SHAREPOINT_ONLINE, CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_sharepoint_sign_out_inactive_users",
        "name": "Inactive users are signed out of SharePoint Online",
        "description": (
            "Enabling idle session sign-out in SharePoint Online automatically "
            "terminates browser sessions that have been inactive, reducing the "
            "risk of unauthorised access on shared or unattended devices."
        ),
        "remediation": (
            "SharePoint admin centre → Policies → Access control → "
            "Idle session sign-out → Sign out users after: 1 hour. "
            "Or via PowerShell: Set-SPOTenant -SignOutInactiveUsersAfter 01:00:00"
        ),
        "source": _check_sharepoint_sign_out_inactive_users,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_SHAREPOINT_ONLINE],
        "is_cis_benchmark": True,
    },
    # ------------------------------------------------------------------
    # Microsoft Teams (manual-review pending Teams PowerShell client)
    # ------------------------------------------------------------------
    {
        "id": "bp_anon_dialin_cannot_start_meeting",
        "name": "Anonymous users and dial-in callers can't start a meeting",
        "description": (
            "Anonymous and PSTN dial-in participants must wait in the lobby "
            "rather than start meetings unsupervised."
        ),
        "remediation": (
            "Set-CsTeamsMeetingPolicy -Identity Global "
            "-AllowAnonymousUsersToStartMeeting $false "
            "-AllowPSTNUsersToBypassLobby $false"
        ),
        "source": _check_anon_dialin_cannot_start_meeting,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_only_org_can_bypass_lobby",
        "name": "Only people in my org can bypass the lobby",
        "description": (
            "AutoAdmittedUsers should be restricted to EveryoneInCompany so "
            "external participants always wait in the lobby."
        ),
        "remediation": "Set-CsTeamsMeetingPolicy -Identity Global -AutoAdmittedUsers EveryoneInCompany",
        "source": _check_only_org_bypass_lobby,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_invited_users_auto_admitted",
        "name": "Only invited users should be automatically admitted to Teams meetings",
        "description": (
            "AutoAdmittedUsers should be set to InvitedUsers so that only "
            "people who were explicitly invited to a meeting are admitted "
            "automatically; all other participants wait in the lobby."
        ),
        "remediation": "Set-CsTeamsMeetingPolicy -Identity Global -AutoAdmittedUsers InvitedUsers",
        "source": _check_invited_users_auto_admitted,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_dialin_cannot_bypass_lobby",
        "name": "Users dialing in can't bypass the lobby",
        "description": (
            "Dial-in callers must wait in the lobby for explicit admission, "
            "preventing unauthorised drop-ins via PSTN."
        ),
        "remediation": "Set-CsTeamsMeetingPolicy -Identity Global -AllowPSTNUsersToBypassLobby $false",
        "source": _manual_review_factory(
            "bp_dialin_cannot_bypass_lobby",
            "Users dialing in can't bypass the lobby",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | Select AllowPSTNUsersToBypassLobby",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS, CAP_TEAMS_AUDIO_CONF],
    },
    {
        "id": "bp_restrict_dialin_bypass_lobby",
        "name": "Restrict dial-in users from bypassing a meeting lobby",
        "description": (
            "AllowPSTNUsersToBypassLobby should be set to $false so that "
            "PSTN dial-in participants are held in the lobby and must be "
            "explicitly admitted, preventing unauthorised access to meetings."
        ),
        "remediation": (
            "Set-CsTeamsMeetingPolicy -Identity Global "
            "-AllowPSTNUsersToBypassLobby $false"
        ),
        "source": _manual_review_factory(
            "bp_restrict_dialin_bypass_lobby",
            "Restrict dial-in users from bypassing a meeting lobby",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | "
            "Select AllowPSTNUsersToBypassLobby",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS, CAP_TEAMS_AUDIO_CONF],
        "is_cis_benchmark": True,
    },
    {
        "id": "bp_external_participants_no_control",
        "name": "External participants can't give or request control",
        "description": (
            "Preventing external participants from taking control of shared "
            "screens stops a primary social-engineering vector."
        ),
        "remediation": "Set-CsTeamsMeetingPolicy -Identity Global -AllowExternalParticipantGiveRequestControl $false",
        "source": _check_external_participants_no_control,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_external_users_cannot_initiate",
        "name": "External Teams users cannot initiate conversations",
        "description": (
            "Restricting federation prevents unsolicited messages from "
            "arbitrary external Teams tenants from reaching internal users."
        ),
        "remediation": (
            "Set-CsTenantFederationConfiguration -AllowFederatedUsers $false "
            "(or restrict via -AllowedDomains to a managed list)"
        ),
        "source": _check_external_users_cannot_initiate,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_teams_external_files_approved_storage",
        "name": "External file sharing in Teams is enabled for only approved cloud storage services",
        "description": (
            "Disabling third-party storage providers in Teams keeps file "
            "sharing within OneDrive/SharePoint where DLP applies."
        ),
        "remediation": (
            "Set-CsTeamsClientConfiguration -Identity Global "
            "-AllowDropBox $false -AllowGoogleDrive $false -AllowBox $false "
            "-AllowShareFile $false -AllowEgnyte $false"
        ),
        "source": _check_teams_external_files_approved_storage,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_restrict_anon_users_join_meeting",
        "name": "Restrict anonymous users from joining meetings",
        "description": (
            "AllowAnonymousUsersToJoinMeeting should be set to $false so that "
            "unauthenticated participants cannot join Teams meetings, reducing "
            "the risk of uninvited attendees and data exposure."
        ),
        "remediation": (
            "Set-CsTeamsMeetingPolicy -Identity Global "
            "-AllowAnonymousUsersToJoinMeeting $false"
        ),
        "source": _check_restrict_anon_users_join_meeting,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
    },
    {
        "id": "bp_restrict_anon_users_start_meeting",
        "name": "Restrict anonymous users from starting Teams meetings",
        "description": (
            "AllowAnonymousUsersToStartMeeting should be set to $false so that "
            "unauthenticated participants cannot start Teams meetings without an "
            "authenticated organizer being present, reducing the risk of "
            "unsupervised meetings and data exposure."
        ),
        "remediation": (
            "Set-CsTeamsMeetingPolicy -Identity Global "
            "-AllowAnonymousUsersToStartMeeting $false"
        ),
        "source": _check_restrict_anon_users_start_meeting,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
        "requires_teams_manage_as_app": True,
        "is_cis_benchmark": True,
    },
    # ------------------------------------------------------------------
    # Defender / Purview
    # ------------------------------------------------------------------
    {
        "id": "bp_safe_links_office_apps",
        "name": "Safe Links for Office applications is enabled",
        "description": (
            "Defender for Office 365 Safe Links rewrites URLs in Office apps "
            "and Teams so they are scanned at click-time."
        ),
        "remediation": (
            "New-SafeLinksPolicy -Name 'Strict Safe Links' "
            "-EnableSafeLinksForOffice $true -TrackClicks $true -AllowClickThrough $false; "
            "New-SafeLinksRule -Name 'Strict Safe Links' -SafeLinksPolicy 'Strict Safe Links' "
            "-RecipientDomainIs (Get-AcceptedDomain).Name"
        ),
        "source": _check_safe_links_office_apps,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_DEFENDER_O365_P1],
    },
    {
        "id": "bp_dlp_policies_enabled",
        "name": "DLP policies are enabled",
        "description": (
            "At least one Microsoft Purview DLP policy must be enabled to "
            "protect sensitive information across Microsoft 365 workloads."
        ),
        "remediation": (
            "Microsoft Purview portal → Data loss prevention → Policies → "
            "Create policy → use the recommended templates for your jurisdiction."
        ),
        "source": _manual_review_factory(
            "bp_dlp_policies_enabled",
            "DLP policies are enabled",
            "Manual verification required. Run: Get-DlpCompliancePolicy | Where Mode -eq 'Enable'",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_PURVIEW_DLP],
    },
    {
        "id": "bp_dlp_policies_teams",
        "name": "DLP policies are enabled for Microsoft Teams",
        "description": (
            "DLP coverage of Teams chat and channel messages prevents "
            "sensitive data leakage via collaboration."
        ),
        "remediation": (
            "Microsoft Purview → DLP → New policy → include Teams chat and "
            "channel messages location → enable in production mode."
        ),
        "source": _manual_review_factory(
            "bp_dlp_policies_teams",
            "DLP policies are enabled for Microsoft Teams",
            "Manual verification required. Run: Get-DlpCompliancePolicy | Where TeamsLocation -ne $null",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_PURVIEW_DLP, CAP_TEAMS],
    },
    {
        "id": "bp_zap_teams_on",
        "name": "Zero-hour auto purge for Microsoft Teams is on",
        "description": (
            "ZAP retroactively removes malicious messages discovered after "
            "delivery, reducing dwell time of Teams-borne threats."
        ),
        "remediation": "Set-TeamsProtectionPolicy -Identity 'Teams Protection Policy' -ZapEnabled $true",
        "source": _check_zap_teams_on,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_DEFENDER_O365_P2, CAP_TEAMS],
    },
    # ------------------------------------------------------------------
    # DNS / on-prem
    # ------------------------------------------------------------------
    {
        "id": "bp_spf_records_published",
        "name": "SPF records are published for all Exchange Online domains",
        "description": (
            "An SPF TXT record must exist for every sending domain so "
            "recipients can verify that mail is sent from authorised servers."
        ),
        "remediation": (
            "At your DNS registrar publish a TXT record for the domain root: "
            "v=spf1 include:spf.protection.outlook.com -all"
        ),
        "source": _check_spf_records_published,
        "source_type": "graph",
        "uses_company_email_domains": True,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_dmarc_records_published",
        "name": "DMARC records for all MyPortal Email domains are published",
        "description": (
            "DMARC policies tell receivers what to do with mail that fails "
            "SPF/DKIM and provides aggregate reporting on spoof attempts."
        ),
        "remediation": (
            "At your DNS registrar publish a TXT record at _dmarc.<domain>: "
            "v=DMARC1; p=quarantine; rua=<copy the company-specific value from DMARC Reporting>"
        ),
        "source": _check_dmarc_records_published,
        "source_type": "graph",
        "uses_company_email_domains": True,
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_onprem_password_protection",
        "name": "Password protection is enabled for on-prem Active Directory",
        "description": (
            "Microsoft Entra Password Protection extends global and custom "
            "banned password lists to on-prem AD via DC agents."
        ),
        "remediation": (
            "Install the Azure AD Password Protection proxy and DC agents on "
            "every domain controller, then in Entra portal → Authentication "
            "methods → Password protection → set 'Mode' to Enforced and "
            "'Enable password protection on Windows Server Active Directory' to Yes."
        ),
        "source": _check_onprem_password_protection,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_ENTRA_ID_P1],
    },
    # ------------------------------------------------------------------
    # CIS Microsoft Intune for Windows Benchmark checks
    # ------------------------------------------------------------------
    {
        "id": "intune_windows_compliance_policy_exists",
        "name": "Windows compliance policy exists",
        "description": (
            "At least one Windows device compliance policy must be configured "
            "in Microsoft Intune to enforce security baselines on managed devices."
        ),
        "remediation": (
            "Create at least one Windows device compliance policy: "
            "Intune → Devices → Compliance policies → Create policy → Windows 10 and later."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_windows",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_windows_encryption",
        "name": "BitLocker encryption required (Windows)",
        "description": (
            "Windows compliance policies should require BitLocker disk encryption "
            "to protect data on managed devices."
        ),
        "remediation": (
            "Create a Windows device compliance policy requiring BitLocker encryption: "
            "Intune → Devices → Compliance policies → Create policy → Windows 10/11 → "
            "System Security → Require BitLocker = Require."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_windows",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_windows_firewall",
        "name": "Windows Firewall required",
        "description": (
            "Windows compliance policies should require the Windows Firewall "
            "to be enabled on managed devices."
        ),
        "remediation": (
            "Require Windows Firewall in the device compliance policy: "
            "Intune → Devices → Compliance policies → Windows policy → "
            "System Security → Firewall = Require."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_windows",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_windows_antivirus",
        "name": "Antivirus required (Windows)",
        "description": (
            "Windows compliance policies should require antivirus software "
            "to be active on managed devices."
        ),
        "remediation": (
            "Require antivirus in the Windows device compliance policy: "
            "Intune → Devices → Compliance policies → Windows policy → "
            "System Security → Antivirus = Require."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_windows",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_windows_secure_boot",
        "name": "Secure Boot required (Windows)",
        "description": (
            "Windows compliance policies should require Secure Boot to be "
            "enabled, protecting against low-level firmware attacks."
        ),
        "remediation": (
            "Require Secure Boot in the Windows device compliance policy: "
            "Intune → Devices → Compliance policies → Windows policy → "
            "System Security → Secure Boot enabled = Require."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_windows",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_windows_min_os",
        "name": "Minimum OS version configured (Windows)",
        "description": (
            "Windows compliance policies should specify a minimum supported "
            "OS version to prevent out-of-date devices from accessing corporate resources."
        ),
        "remediation": (
            "Set a minimum supported OS version in the Windows compliance policy: "
            "Intune → Devices → Compliance policies → Windows policy → "
            "Device Properties → Minimum OS version."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_windows",
        "default_enabled": True,
        "has_remediation": False,
    },
    # ------------------------------------------------------------------
    # CIS Microsoft Intune for iOS/iPadOS Benchmark checks
    # ------------------------------------------------------------------
    {
        "id": "intune_ios_compliance_policy_exists",
        "name": "iOS/iPadOS compliance policy exists",
        "description": (
            "At least one iOS/iPadOS device compliance policy must be configured "
            "in Microsoft Intune to enforce security baselines on managed devices."
        ),
        "remediation": (
            "Create at least one iOS/iPadOS device compliance policy: "
            "Intune → Devices → Compliance policies → Create policy → iOS/iPadOS."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_ios",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_ios_passcode_required",
        "name": "Passcode required (iOS/iPadOS)",
        "description": (
            "iOS/iPadOS compliance policies should require a passcode/PIN "
            "to protect device access."
        ),
        "remediation": (
            "Require a passcode/PIN in the iOS compliance policy: "
            "Intune → Compliance policies → iOS policy → System Security → Require a password."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_ios",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_ios_jailbreak_blocked",
        "name": "Jailbroken devices blocked (iOS/iPadOS)",
        "description": (
            "iOS/iPadOS compliance policies should block jailbroken devices "
            "which bypass Apple's security controls."
        ),
        "remediation": (
            "Block jailbroken devices in the iOS compliance policy: "
            "Intune → Compliance policies → iOS policy → Device Health → "
            "Jailbroken devices = Block."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_ios",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_ios_min_os",
        "name": "Minimum OS version configured (iOS/iPadOS)",
        "description": (
            "iOS/iPadOS compliance policies should specify a minimum supported "
            "OS version to prevent outdated devices from accessing corporate resources."
        ),
        "remediation": (
            "Set a minimum supported iOS version in the compliance policy: "
            "Intune → Compliance policies → iOS policy → Device Properties → Minimum OS version."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_ios",
        "default_enabled": True,
        "has_remediation": False,
    },
    # ------------------------------------------------------------------
    # CIS Microsoft Intune for macOS Benchmark checks
    # ------------------------------------------------------------------
    {
        "id": "intune_macos_compliance_policy_exists",
        "name": "macOS compliance policy exists",
        "description": (
            "At least one macOS device compliance policy must be configured "
            "in Microsoft Intune to enforce security baselines on managed devices."
        ),
        "remediation": (
            "Create at least one macOS device compliance policy: "
            "Intune → Devices → Compliance policies → Create policy → macOS."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_macos",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_macos_filevault",
        "name": "FileVault disk encryption required (macOS)",
        "description": (
            "macOS compliance policies should require FileVault disk encryption "
            "to protect data on managed Mac devices."
        ),
        "remediation": (
            "Require FileVault disk encryption in the macOS compliance policy: "
            "Intune → Compliance policies → macOS policy → System Security → "
            "Require encryption of data storage on device."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_macos",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_macos_firewall",
        "name": "macOS Firewall required",
        "description": (
            "macOS compliance policies should require the macOS Firewall "
            "to be enabled on managed Mac devices."
        ),
        "remediation": (
            "Require the macOS Firewall in the compliance policy: "
            "Intune → Compliance policies → macOS policy → System Security → Firewall."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_macos",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_macos_min_os",
        "name": "Minimum OS version configured (macOS)",
        "description": (
            "macOS compliance policies should specify a minimum supported "
            "OS version to prevent outdated Mac devices from accessing corporate resources."
        ),
        "remediation": (
            "Set a minimum supported macOS version in the compliance policy: "
            "Intune → Compliance policies → macOS policy → Device Properties → Minimum OS version."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_macos",
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "intune_macos_gatekeeper",
        "name": "Gatekeeper enabled (macOS)",
        "description": (
            "macOS compliance policies should require Gatekeeper to be enabled, "
            "ensuring only trusted software can run on managed Mac devices."
        ),
        "remediation": (
            "Require Gatekeeper in the macOS compliance policy: "
            "Intune → Compliance policies → macOS policy → System Security → Gatekeeper."
        ),
        "is_cis_benchmark": True,
        "cis_group": "intune_macos",
        "default_enabled": True,
        "has_remediation": False,
    },
]

# All Intune-grouped checks require a Microsoft Intune license; mark them
# automatically so the catalog stays DRY.
for _bp in _BEST_PRACTICES:
    if _bp.get("cis_group", "").startswith("intune_") and "requires_licenses" not in _bp:
        _bp["requires_licenses"] = [CAP_INTUNE]

# Checks implemented via manual-review runners have no automation support in
# MyPortal yet; keep them disabled by default across all companies.
for _bp in _BEST_PRACTICES:
    source = _bp.get("source")
    source_name = getattr(source, "__name__", "")
    if isinstance(source_name, str) and source_name.endswith("_manual"):
        _bp["default_enabled"] = False

# Mapping from cis_group name to the batch runner function from cis_benchmark.py
_CIS_GROUP_RUNNERS: dict[str, Callable[..., Any]] = {
    "intune_windows": run_intune_windows_benchmarks,
    "intune_ios": run_intune_ios_benchmarks,
    "intune_macos": run_intune_macos_benchmarks,
}

_BATCH_REMEDIATION_SCOPES: dict[str, str] = {
    "m365": "Microsoft 365",
    "intune_windows": "CIS Intune Benchmark – Windows",
    "intune_ios": "CIS Intune Benchmark – iOS / iPadOS",
    "intune_macos": "CIS Intune Benchmark – macOS",
}

_CRITICAL_RISK_CHECK_IDS = frozenset(
    {
        "bp_block_legacy_auth",
        "bp_disable_direct_send",
        "bp_per_user_mfa_disabled",
        "bp_smtp_auth_disabled",
        "bp_automatic_email_forwarding",
        "bp_weak_auth_methods_disabled",
        "bp_authenticator_mfa_fatigue",
        "bp_internal_phishing_forms",
    }
)
_RISK_SCORE_BY_SEVERITY = {
    "low": 20,
    "medium": 45,
    "high": 70,
    "critical": 90,
}
_STATUS_PRIORITY_ORDER = {
    STATUS_FAIL: 0,
    STATUS_UNKNOWN: 1,
    STATUS_PASS: 2,
    STATUS_NOT_APPLICABLE: 3,
}
_REGRESSION_NOTICE = (
    "Regression detected: this check changed from pass to fail since the last successful evaluation."
)


def _benchmark_category_label(bp: Mapping[str, Any]) -> str:
    return _BATCH_REMEDIATION_SCOPES.get(str(bp.get("cis_group") or "").strip(), "Microsoft 365")


def _batch_scope_for_bp(bp: Mapping[str, Any]) -> str:
    cis_group = str(bp.get("cis_group") or "").strip()
    return cis_group if cis_group in _BATCH_REMEDIATION_SCOPES else "m365"


def _risk_severity_for_bp(bp: Mapping[str, Any]) -> str:
    check_id = str(bp.get("id") or "")
    if check_id in _CRITICAL_RISK_CHECK_IDS:
        return "critical"
    if check_id.startswith("bp_monitor_"):
        return "low"
    if str(bp.get("cis_group") or "").startswith("intune_"):
        return "medium"
    if bp.get("has_remediation"):
        return "high"
    return "medium"


def _business_impact_for_bp(bp: Mapping[str, Any], severity: str) -> str:
    check_id = str(bp.get("id") or "")
    if check_id.startswith("bp_monitor_"):
        return "Monitoring gap can delay detection, escalation, and executive reporting."
    if str(bp.get("cis_group") or "").startswith("intune_"):
        return "Endpoint compliance drift can expand device access and policy exposure."
    if severity == "critical":
        return "Control failure can enable tenant compromise, account takeover, or high-impact email abuse."
    if severity == "high":
        return "Control gap weakens identity, messaging, or data-protection safeguards across the tenant."
    return "Configuration drift increases operational risk and should be prioritised during the next change window."


def _remediation_runbook_for_bp(bp: Mapping[str, Any]) -> list[str]:
    runbook = [
        f"Confirm the failure is in scope for this company and {_benchmark_category_label(bp)}.",
    ]
    if bp.get("has_remediation"):
        runbook.append(
            "Review prerequisites, approvals, and any maintenance-window impact before using automated remediation."
        )
    remediation = str(bp.get("remediation") or "").strip()
    if remediation:
        runbook.append(remediation)
    runbook.append(
        "Re-run the check after the change and document the outcome in the related ticket or note."
    )
    return runbook


def _rollback_guidance_for_bp(bp: Mapping[str, Any]) -> str:
    if str(bp.get("cis_group") or "").startswith("intune_"):
        target = "Intune policy or compliance profile"
    elif bp.get("source_type") == "exo":
        target = "Exchange Online setting or policy"
    elif bp.get("source_type") == "scc":
        target = "Purview / Compliance policy"
    else:
        target = "Microsoft 365 or Entra policy"
    return (
        f"Capture the current {target} values before remediation. If the change causes user impact, "
        "restore the prior configuration from the recorded baseline or change ticket, then re-run the "
        "check to confirm the rollback."
    )


def _posture_metadata_for_bp(bp: Mapping[str, Any]) -> dict[str, Any]:
    severity = _risk_severity_for_bp(bp)
    return {
        "risk_severity": severity,
        "risk_score": _RISK_SCORE_BY_SEVERITY[severity],
        "business_impact": _business_impact_for_bp(bp, severity),
        "benchmark_category": _benchmark_category_label(bp),
        "batch_scope": _batch_scope_for_bp(bp),
        "remediation_runbook": _remediation_runbook_for_bp(bp),
        "rollback_guidance": _rollback_guidance_for_bp(bp),
    }


def _is_regression(previous_status: str | None, status: str) -> bool:
    return previous_status == STATUS_PASS and status == STATUS_FAIL


def _with_regression_notice(details: str, *, previous_status: str | None, status: str) -> str:
    if not _is_regression(previous_status, status):
        return details
    if _REGRESSION_NOTICE in details:
        return details
    return f"{_REGRESSION_NOTICE} {details}".strip()


def _sort_results_by_priority(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        results,
        key=lambda item: (
            _STATUS_PRIORITY_ORDER.get(str(item.get("status") or ""), 4),
            -int(item.get("risk_score") or 0),
            str(item.get("check_name") or ""),
        ),
    )


def get_batch_remediation_scopes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    for scope_id, label in _BATCH_REMEDIATION_SCOPES.items():
        pending = sum(
            1
            for result in results
            if str(result.get("batch_scope") or "m365") == scope_id
            and result.get("status") == STATUS_FAIL
            and result.get("has_remediation")
        )
        if pending:
            scopes.append({"id": scope_id, "label": label, "pending_count": pending})
    return scopes


def _enrich_catalog_entry(bp: dict[str, Any]) -> dict[str, Any]:
    """Return a public-facing copy of a catalog entry with internal keys
    stripped and license requirements rendered as a human-friendly string.
    """
    entry = {k: v for k, v in bp.items() if k not in _INTERNAL_KEYS}
    entry.update(_posture_metadata_for_bp(bp))
    requires = bp.get("requires_licenses") or []
    if requires:
        entry["requires_licenses_display"] = _format_missing_licenses(requires)
    return entry


def list_best_practices() -> list[dict[str, Any]]:
    """Return the best-practice catalog (without internal runner keys)."""
    return [_enrich_catalog_entry(bp) for bp in _BEST_PRACTICES]


def _catalog_map() -> dict[str, dict[str, Any]]:
    return {bp["id"]: bp for bp in _BEST_PRACTICES}


def get_remediation(check_id: str) -> str:
    bp = _catalog_map().get(check_id)
    if bp:
        return bp["remediation"]
    return "Consult Microsoft 365 documentation for remediation guidance."


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


async def get_enabled_check_ids() -> set[str]:
    """Return the set of best-practice check_ids currently enabled globally.

    Checks that have never been recorded in the settings table fall back to
    their ``default_enabled`` value from the catalog.
    """
    settings = await bp_repo.get_settings_map()
    enabled: set[str] = set()
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id in settings:
            if settings[check_id]["enabled"]:
                enabled.add(check_id)
        elif bp.get("default_enabled", True):
            enabled.add(check_id)
    return enabled


async def get_auto_remediate_check_ids() -> set[str]:
    """Return the set of check_ids that have auto-remediation enabled globally.

    Only checks that declare ``has_remediation: True`` in the catalog can
    meaningfully appear in this set; others are excluded even if the settings
    row has ``auto_remediate=True``.
    """
    settings = await bp_repo.get_settings_map()
    auto_remediate: set[str] = set()
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        auto_remediate_enabled = (
            settings[check_id].get("auto_remediate")
            if check_id in settings
            else bool(bp.get("default_auto_remediate", False))
        )
        if bp.get("has_remediation") and auto_remediate_enabled:
            auto_remediate.add(check_id)
    return auto_remediate


async def get_create_ticket_on_fail_check_ids() -> set[str]:
    """Return the set of check_ids that should create tickets on pass→fail."""
    settings = await bp_repo.get_settings_map()
    return {
        bp["id"]
        for bp in _BEST_PRACTICES
        if bp["id"] in settings and settings[bp["id"]].get("create_ticket_on_fail")
    }


async def reset_enabled_results_to_unknown(company_id: int) -> int:
    """Set all enabled (non-excluded) checks to Unknown for ``company_id``."""
    enabled = await get_enabled_check_ids()
    excluded = await bp_repo.get_company_exclusions(company_id)
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    reset_count = 0

    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id not in enabled or check_id in excluded:
            continue
        await bp_repo.upsert_result(
            company_id=company_id,
            check_id=check_id,
            check_name=bp["name"],
            status=STATUS_UNKNOWN,
            details="Evaluation in progress.",
            run_at=run_at,
        )
        reset_count += 1
    return reset_count


async def list_settings_with_catalog(company_id: int | None = None) -> list[dict[str, Any]]:
    """Return the catalog merged with the current global enabled and auto-remediate flags.

    Each item contains the catalog metadata plus:
    - ``enabled`` boolean (global on/off, defaulting to ``default_enabled``)
    - ``auto_remediate`` boolean (auto-remediation after each evaluation)
    - ``create_ticket_on_fail`` boolean (ticket created on pass→fail)
    - ``excluded`` boolean (per-company exclusion; only set when ``company_id`` is given)
    """
    settings = await bp_repo.get_settings_map()
    excluded_ids: set[str] = set()
    if company_id is not None:
        excluded_ids = await bp_repo.get_company_exclusions(company_id)
    out: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        entry = _enrich_catalog_entry(bp)
        row = settings.get(bp["id"])
        entry["enabled"] = row.get("enabled") if row else bool(bp.get("default_enabled", True))
        entry["auto_remediate"] = (
            row.get("auto_remediate", False)
            if row
            else bool(bp.get("default_auto_remediate", False))
        )
        entry["create_ticket_on_fail"] = (
            row.get("create_ticket_on_fail", False) if row else False
        )
        entry["excluded"] = bp["id"] in excluded_ids
        out.append(entry)
    return out


async def set_enabled_checks(
    enabled_check_ids: set[str],
    auto_remediate_check_ids: set[str] | None = None,
    create_ticket_on_fail_check_ids: set[str] | None = None,
) -> None:
    """Persist the global enabled and auto-remediate flags for every catalog check.

    ``enabled_check_ids`` controls which checks are active globally.
    ``auto_remediate_check_ids`` controls which checks trigger automated
    remediation immediately after evaluation (only honoured for checks that
    declare ``has_remediation: True`` in the catalog).
    ``create_ticket_on_fail_check_ids`` controls which checks create a ticket
    when their status changes from pass to fail.

    For checks toggled off, any previously-stored per-company results are
    cleared so they no longer appear on company pages.
    """
    catalog = _catalog_map()
    enabled_filtered = {cid for cid in enabled_check_ids if cid in catalog}
    auto_remediate_filtered: set[str] = set()
    if auto_remediate_check_ids is not None:
        auto_remediate_filtered = {
            cid
            for cid in auto_remediate_check_ids
            if cid in catalog and catalog[cid].get("has_remediation")
        }
    create_ticket_filtered: set[str] = set()
    if create_ticket_on_fail_check_ids is not None:
        create_ticket_filtered = {
            cid for cid in create_ticket_on_fail_check_ids if cid in catalog
        }
    else:
        existing_settings = await bp_repo.get_settings_map()
        create_ticket_filtered = {
            cid
            for cid, row in existing_settings.items()
            if cid in catalog and row.get("create_ticket_on_fail")
        }
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        is_enabled = check_id in enabled_filtered
        is_auto_remediate = check_id in auto_remediate_filtered
        should_create_ticket = check_id in create_ticket_filtered
        await bp_repo.upsert_setting(
            check_id=check_id,
            enabled=is_enabled,
            auto_remediate=is_auto_remediate,
            create_ticket_on_fail=should_create_ticket,
        )
        if not is_enabled:
            await bp_repo.delete_result_for_check(check_id)
    log_info(
        "M365 Best Practice settings updated",
        enabled_count=len(enabled_filtered),
        auto_remediate_count=len(auto_remediate_filtered),
        create_ticket_on_fail_count=len(create_ticket_filtered),
        total=len(_BEST_PRACTICES),
    )


def build_failure_ticket_external_reference(company_id: int, check_id: str) -> str:
    return f"m365-best-practice:{company_id}:{check_id}"


def build_failure_ticket_subject(
    check_name: str, *, regression_detected: bool = False
) -> str:
    prefix = "M365 posture regression" if regression_detected else "M365 best practice failed"
    return f"{prefix}: {check_name}"[:255]


def _format_run_timestamp(run_at: datetime) -> str:
    return run_at.strftime("%Y-%m-%d %H:%M:%S UTC")


def build_failure_ticket_description(
    *,
    company_name: str,
    check_id: str,
    check_name: str,
    details: str,
    run_at: datetime | None,
    created_automatically: bool,
    regression_detected: bool = False,
    requester_name: str | None = None,
    requester_email: str | None = None,
) -> str:
    bp = _catalog_map().get(check_id, {"id": check_id})
    posture = _posture_metadata_for_bp(bp)
    if regression_detected:
        intro = (
            "This ticket was created automatically because an M365 best-practice "
            "check regressed from <strong>Pass</strong> to <strong>Fail</strong>."
        )
    elif created_automatically:
        intro = (
            "This ticket was created automatically because an M365 best-practice "
            "check failed and requires review."
        )
    else:
        intro = "A portal user requested technician assistance for a failed M365 best-practice check."
    metadata_lines = [f"<strong>Company:</strong> {escape(company_name)}"]
    if requester_name:
        metadata_lines.append(f"<strong>Requester:</strong> {escape(requester_name)}")
    if requester_email:
        metadata_lines.append(f"<strong>Requester email:</strong> {escape(requester_email)}")
    metadata_lines.extend(
        [
            f"<strong>Check:</strong> {escape(check_name)}",
            f"<strong>Check ID:</strong> {escape(check_id)}",
        ]
    )
    if posture:
        metadata_lines.extend(
            [
                f"<strong>Benchmark category:</strong> {escape(str(posture.get('benchmark_category') or 'Microsoft 365'))}",
                (
                    "<strong>Risk priority:</strong> "
                    f"{escape(str(posture.get('risk_severity') or 'medium').title())} "
                    f"({escape(str(posture.get('risk_score') or 0))}/100)"
                ),
                f"<strong>Business impact:</strong> {escape(str(posture.get('business_impact') or ''))}",
            ]
        )
    if run_at is not None:
        metadata_lines.append(
            f"<strong>Evaluated at:</strong> {escape(_format_run_timestamp(run_at))}"
        )
    runbook = posture.get("remediation_runbook") or []
    return (
        f"<p>{intro}</p>"
        f"<p>{'<br />'.join(metadata_lines)}</p>"
        f"<h3>Failure details</h3><p>{escape(details or 'No details provided.')}</p>"
        + (
            "<h3>Recommended runbook</h3><ol>"
            + "".join(f"<li>{escape(str(step))}</li>" for step in runbook)
            + "</ol>"
            if runbook
            else ""
        )
        + (
            f"<h3>Rollback guidance</h3><p>{escape(str(posture.get('rollback_guidance') or ''))}</p>"
            if posture.get("rollback_guidance")
            else ""
        )
    )


async def _maybe_create_ticket_on_fail(
    *,
    company_id: int,
    check_id: str,
    check_name: str,
    status: str,
    details: str,
    run_at: datetime,
    previous_status: str | None,
    create_ticket_on_fail_ids: set[str],
) -> None:
    if (
        status != STATUS_FAIL
        or previous_status != STATUS_PASS
        or check_id not in create_ticket_on_fail_ids
    ):
        return

    external_reference = build_failure_ticket_external_reference(company_id, check_id)
    existing_ticket = await tickets_repo.find_open_ticket_by_external_reference(
        external_reference
    )
    if existing_ticket:
        log_info(
            "M365 best practice failure ticket already open",
            company_id=company_id,
            check_id=check_id,
            ticket_id=existing_ticket.get("id"),
        )
        return

    company = await companies_repo.get_company_by_id(company_id)
    company_name = (
        str(company.get("name") or f"Company {company_id}")
        if company
        else f"Company {company_id}"
    )
    description = build_failure_ticket_description(
        company_name=company_name,
        check_id=check_id,
        check_name=check_name,
        details=details,
        run_at=run_at,
        created_automatically=True,
        regression_detected=_is_regression(previous_status, status),
    )
    try:
        ticket = await tickets_service.create_ticket(
            subject=build_failure_ticket_subject(
                check_name,
                regression_detected=_is_regression(previous_status, status),
            ),
            description=description,
            requester_id=None,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status=await tickets_service.resolve_status_or_default(None),
            category=_M365_FAILURE_TICKET_CATEGORY,
            module_slug=_M365_FAILURE_TICKET_MODULE,
            external_reference=external_reference,
            trigger_automations=True,
            send_creation_notification=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log_error(
            "Failed to create M365 best practice failure ticket",
            company_id=company_id,
            check_id=check_id,
            error=str(exc),
        )
        return

    log_info(
        "M365 best practice failure ticket created",
        company_id=company_id,
        check_id=check_id,
        ticket_id=ticket.get("id"),
    )


async def save_company_exclusions(company_id: int, excluded_check_ids: set[str]) -> None:
    """Persist the per-company check exclusions for ``company_id``.

    Only check_ids present in the catalog are accepted; unknown ids are
    silently ignored.
    """
    catalog = _catalog_map()
    filtered = {cid for cid in excluded_check_ids if cid in catalog}
    await bp_repo.set_company_exclusions(company_id, filtered)
    # Clear any previously-stored results for newly-excluded checks for this
    # company only so they no longer appear on the company's page.
    for check_id in filtered:
        await bp_repo.delete_result_for_check_and_company(company_id, check_id)
    log_info(
        "M365 best practice company exclusions updated",
        company_id=company_id,
        excluded_count=len(filtered),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# HTTP status codes that indicate a transient Microsoft Graph / Exchange Online
# failure where retrying the request is likely to succeed.  Permanent errors
# (e.g. 400 bad request, 401/403 auth/permission, 404 not found) are NOT
# retried because retrying cannot turn them into the real check result – those
# need real remediation (granting permissions, fixing configuration, etc.) and
# the catalog's static remediation text already covers them.
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

# Maximum number of attempts (initial + retries) for a single check request.
_MAX_CHECK_ATTEMPTS = 3

# Base delay (seconds) for exponential backoff between retries.  Actual delays
# are 1s, 2s, 4s, … up to ``_MAX_RETRY_DELAY``.
_RETRY_BASE_DELAY = 1.0
_MAX_RETRY_DELAY = 8.0


def _retry_backoff_seconds(attempt: int) -> float:
    """Return the exponential-backoff delay (seconds) before the next retry.

    ``attempt`` is the 1-indexed attempt number that has just failed (so
    ``attempt=1`` is the delay before the first retry, producing 1s, 2s, 4s,
    … capped at :data:`_MAX_RETRY_DELAY`).
    """
    return min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _MAX_RETRY_DELAY)

# Pre-compiled regex matching a retryable HTTP status code embedded in an
# ``M365Error`` message (e.g. ``"Microsoft Graph request failed (429)"``).
_RETRYABLE_STATUS_IN_DETAILS = re.compile(
    r"\(\s*(?:" + "|".join(str(s) for s in sorted(_RETRYABLE_HTTP_STATUSES)) + r")\s*\)"
)

# Substrings in a check's ``details`` that indicate a transient request-level
# failure even when no HTTP status code is present (e.g. network / decode
# errors raised by httpx).  Keep this list narrow so legitimately-unknown
# results that happen for *non-transient* reasons are not retried.
_TRANSIENT_DETAIL_MARKERS: tuple[str, ...] = (
    "decode error",
    "response parse error",
    "request decode error",
)


def _is_retryable_m365_error(exc: M365Error) -> bool:
    """Return True if ``exc`` represents a transient failure worth retrying.

    Treats network-level / decode errors (no HTTP status attached) and the
    standard transient HTTP statuses as retryable.  Permanent client errors
    (400, 401, 403, 404, etc.) are not retried.
    """
    status = getattr(exc, "http_status", None)
    if status is None:
        return True
    return status in _RETRYABLE_HTTP_STATUSES


def _result_indicates_transient_failure(result: Any) -> bool:
    """Return True when an unknown check result looks like a transient failure.

    The underlying check helpers (in ``cis_benchmark`` and this module) catch
    :class:`M365Error` themselves and return a ``STATUS_UNKNOWN`` result whose
    ``details`` embeds the original error message.  This helper inspects that
    message to decide whether the failure was transient (worth retrying) or
    permanent / informational (a real "we cannot determine this" answer).

    For batch runners that return a ``list`` of result dicts (e.g. the Intune
    benchmark groups), the list is treated as transient when *every* item is
    an unknown result with a transient marker – this is the shape produced
    when the batch's top-level Graph call fails and propagates the same
    error to every check in the group.
    """
    if isinstance(result, list):
        if not result:
            return False
        return all(_result_indicates_transient_failure(item) for item in result)
    if not isinstance(result, dict):
        return False
    if result.get("status") != STATUS_UNKNOWN:
        return False
    details = result.get("details") or ""
    if not isinstance(details, str) or not details:
        return False
    if _RETRYABLE_STATUS_IN_DETAILS.search(details):
        return True
    lowered = details.lower()
    return any(marker in lowered for marker in _TRANSIENT_DETAIL_MARKERS)


async def _call_check_with_retry(
    factory: Callable[[], Awaitable[Any]],
    *,
    company_id: int,
    check_id: str,
    max_attempts: int = _MAX_CHECK_ATTEMPTS,
) -> Any:
    """Invoke ``factory()`` with retry on transient failures.

    ``factory`` must be a zero-argument callable that returns a fresh awaitable
    each time it is invoked (so each attempt issues a new HTTP request).

    Two retry signals are honoured:

    * A raised :class:`M365Error` whose HTTP status is transient (or absent,
      which indicates a network/decode error).  Permanent statuses re-raise
      immediately.
    * A returned result dict whose ``status`` is ``STATUS_UNKNOWN`` and whose
      ``details`` embeds a transient HTTP status or network/parse error
      marker.  Many check helpers already swallow :class:`M365Error` and
      return such a dict, so this lets us retry them transparently.

    On the final attempt the most recent outcome is returned (or re-raised)
    unchanged so the caller can record the underlying error message in the
    persisted result.
    """
    last_exc: M365Error | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await factory()
        except M365Error as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable_m365_error(exc):
                raise
            delay = _retry_backoff_seconds(attempt)
            log_info(
                "M365 best practice check transient failure – retrying",
                company_id=company_id,
                check_id=check_id,
                attempt=attempt,
                max_attempts=max_attempts,
                http_status=getattr(exc, "http_status", None),
                graph_error_code=getattr(exc, "graph_error_code", None),
                retry_in_seconds=delay,
            )
            await asyncio.sleep(delay)
            continue

        if attempt >= max_attempts or not _result_indicates_transient_failure(result):
            return result

        delay = _retry_backoff_seconds(attempt)
        log_info(
            "M365 best practice check returned transient unknown – retrying",
            company_id=company_id,
            check_id=check_id,
            attempt=attempt,
            max_attempts=max_attempts,
            details=(result.get("details") if isinstance(result, dict) else None),
            retry_in_seconds=delay,
        )
        await asyncio.sleep(delay)

    # Defensive: loop always either returns or raises, but keep mypy/static
    # analysers happy in case max_attempts is somehow <= 0.
    if last_exc is not None:
        raise last_exc
    raise M365Error(f"Best practice check '{check_id}' produced no result")


async def run_best_practices(
    company_id: int,
    *,
    previous_statuses: Mapping[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Run all globally-enabled best-practice checks for ``company_id``.

    Returns the list of result dicts (one per check) and persists each result
    in the ``m365_best_practice_results`` table.

    After evaluation, any check that both:
    - returned ``STATUS_FAIL``, and
    - has ``auto_remediate`` enabled globally (and ``has_remediation: True``)

    will have automated remediation triggered immediately.
    Callers that reset stored results before running checks should pass
    ``previous_statuses`` captured before that reset so pass→fail ticket
    detection uses the pre-run state.

    Graph-based checks receive the Graph access token; Exchange-Online-based
    checks (``source_type == "exo"``) receive the EXO token and tenant ID
    acquired once lazily.  CIS Intune checks (``cis_group`` set) are run via
    their batch runner once per group and results cached for the run.
    """
    previous_statuses = dict(previous_statuses or {})
    # Best-practice Graph checks are designed around application permissions.
    # Always use an app-only token to avoid reusing a cached delegated token
    # that may not carry equivalent privileges (e.g. AuditLog.Read.All).
    graph_token = await acquire_access_token(
        company_id, force_client_credentials=True
    )

    # Self-heal: re-apply any missing app role assignments using the stored
    # delegated token from the "Authorise portal access" connect flow.  This
    # handles tenants whose enterprise app service principal is missing
    # required Graph permissions (Policy.Read.All, AuditLog.Read.All,
    # SecurityEvents.Read.All, DeviceManagementConfiguration.Read.All,
    # ReportSettings.ReadWrite.All, …) – typically because the app was
    # provisioned before those permissions were added to the required set.
    # Without this, every Conditional Access / authorization policy / named
    # locations / Secure Score / device compliance check returns 403.
    # Mirrors the pattern used by ``sync_company_licenses`` and
    # ``sync_mailboxes`` for their own 403 self-healing.
    try:
        delegated_token = await acquire_delegated_token(company_id)
    except Exception as exc:  # noqa: BLE001 – self-heal must never raise
        log_error(
            "M365 best practices: failed to acquire delegated token for self-heal",
            company_id=company_id,
            error=str(exc),
        )
        delegated_token = None
    if delegated_token:
        try:
            granted = await try_grant_missing_permissions(
                company_id, access_token=delegated_token
            )
        except Exception as exc:  # noqa: BLE001 – self-heal must never raise
            log_error(
                "M365 best practices: try_grant_missing_permissions raised",
                company_id=company_id,
                error=str(exc),
            )
            granted = False
        if granted:
            log_info(
                "M365 best practices: granted missing app role assignments – "
                "refreshing app access token",
                company_id=company_id,
            )
            graph_token = await acquire_access_token(
                company_id, force_client_credentials=True
            )

    enabled = await get_enabled_check_ids()
    auto_remediate_ids = await get_auto_remediate_check_ids()
    create_ticket_on_fail_ids = await get_create_ticket_on_fail_check_ids()
    try:
        excluded = await bp_repo.get_company_exclusions(company_id)
    except Exception as exc:  # noqa: BLE001 – exclusion lookup must never break the runner
        log_error(
            "M365 best practices: company exclusion lookup failed",
            company_id=company_id,
            error=str(exc),
        )
        excluded = set()
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Detect tenant licensing capabilities once per run.  Returns ``None``
    # when detection fails (e.g., missing Directory.Read.All permission); in
    # that case checks are run as before and never marked N/A.
    tenant_capabilities = await detect_tenant_capabilities(graph_token)

    # EXO token/tenant – acquired lazily on first EXO check
    exo_token: str | None = None
    exo_tenant_id: str | None = None

    # SCC token/tenant – acquired lazily on first SCC (Security & Compliance) check
    scc_token: str | None = None
    scc_tenant_id: str | None = None

    # Cache for CIS batch group results: group_name → {check_id: result_dict}
    cis_group_cache: dict[str, dict[str, dict[str, Any]]] = {}

    results: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id not in enabled or check_id in excluded:
            continue
        check_name = bp["name"]
        previous_status = previous_statuses.get(check_id)
        cis_group = bp.get("cis_group")
        affected_accounts: list[dict[str, str]] = []

        # If the tenant lacks the licenses required to implement this check,
        # mark it as N/A and skip evaluation/auto-remediation entirely.
        missing = _missing_capabilities(bp.get("requires_licenses"), tenant_capabilities)
        if missing:
            status = STATUS_NOT_APPLICABLE
            details = (
                "Not applicable – this check requires the following Microsoft 365 "
                f"license(s) which the tenant does not have: "
                f"{_format_missing_licenses(missing)}."
            )
        elif bp.get("requires_teams_manage_as_app"):
            # Teams PowerShell cmdlet checks require Teams.ManageAsApp which
            # cannot be programmatically assigned to an app registration.
            status = STATUS_NOT_APPLICABLE
            details = _TEAMS_PS_NOT_APPLICABLE_DETAILS
        elif cis_group and cis_group in _CIS_GROUP_RUNNERS:
            # CIS batch check – run the group runner once and cache results
            if cis_group not in cis_group_cache:
                batch_runner = _CIS_GROUP_RUNNERS.get(cis_group)
                if batch_runner:
                    try:
                        batch = await _call_check_with_retry(
                            lambda runner=batch_runner: runner(graph_token),
                            company_id=company_id,
                            check_id=f"cis_group:{cis_group}",
                        )
                        cis_group_cache[cis_group] = {r["check_id"]: r for r in batch}
                    except M365Error as exc:
                        log_error(
                            "CIS Intune benchmark batch failed",
                            company_id=company_id,
                            cis_group=cis_group,
                            error=str(exc),
                        )
                        cis_group_cache[cis_group] = {}
                else:
                    cis_group_cache[cis_group] = {}
            raw = cis_group_cache[cis_group].get(check_id)
            if raw:
                status = raw.get("status", STATUS_UNKNOWN)
                details = raw.get("details") or ""
                affected_accounts = raw.get("affected_accounts") or []
            else:
                status = STATUS_UNKNOWN
                details = "Check result not available from batch run."
        else:
            source_type = bp.get("source_type", "graph")
            runner: BestPracticeRunner = bp["source"]
            try:
                if source_type == "exo":
                    if exo_token is None:
                        exo_token, exo_tenant_id = await _acquire_exo_access_token(company_id)
                    if bp.get("uses_company_email_domains"):
                        email_domains = (
                            await companies_repo.get_email_domains_for_company(company_id)
                        )
                        raw = await _call_check_with_retry(
                            lambda r=runner: r(  # type: ignore[call-arg,misc]
                                exo_token, exo_tenant_id, email_domains
                            ),
                            company_id=company_id,
                            check_id=check_id,
                        )
                    else:
                        raw = await _call_check_with_retry(
                            lambda r=runner: r(exo_token, exo_tenant_id),  # type: ignore[call-arg,misc]
                            company_id=company_id,
                            check_id=check_id,
                        )
                elif source_type == "scc":
                    if scc_token is None:
                        scc_token, scc_tenant_id = await _acquire_scc_access_token(company_id)
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(scc_token, scc_tenant_id),  # type: ignore[call-arg,misc]
                        company_id=company_id,
                        check_id=check_id,
                    )
                else:
                    if bp.get("uses_company_id"):
                        raw = await _call_check_with_retry(
                            lambda r=runner: r(graph_token, company_id),  # type: ignore[call-arg,misc]
                            company_id=company_id,
                            check_id=check_id,
                        )
                    elif bp.get("uses_company_email_domains"):
                        email_domains = await companies_repo.get_email_domains_for_company(company_id)
                        raw = await _call_check_with_retry(
                            lambda r=runner: r(graph_token, email_domains),  # type: ignore[call-arg,misc]
                            company_id=company_id,
                            check_id=check_id,
                        )
                    else:
                        raw = await _call_check_with_retry(
                            lambda r=runner: r(graph_token),  # type: ignore[call-arg,misc]
                            company_id=company_id,
                            check_id=check_id,
                        )
                status = raw.get("status", STATUS_UNKNOWN)
                details = raw.get("details") or ""
                affected_accounts = raw.get("affected_accounts") or []
            except M365Error as exc:
                log_error(
                    "M365 best practice check failed",
                    company_id=company_id,
                    check_id=check_id,
                    error=str(exc),
                )
                status = STATUS_UNKNOWN
                details = f"Unable to evaluate check: {exc}"

        if affected_accounts:
            status, details, affected_accounts = await _apply_account_exclusions(
                company_id, check_id, status, details, affected_accounts
            )
        details = _with_regression_notice(
            details,
            previous_status=previous_status,
            status=status,
        )
        await bp_repo.upsert_result(
            company_id=company_id,
            check_id=check_id,
            check_name=check_name,
            status=status,
            details=details,
            affected_accounts=affected_accounts,
            run_at=run_at,
        )

        # Auto-remediate if the check failed and auto-remediation is enabled
        if status == STATUS_FAIL and check_id in auto_remediate_ids:
            log_info(
                "M365 best practice auto-remediation triggered",
                company_id=company_id,
                check_id=check_id,
            )
            await remediate_check(company_id=company_id, check_id=check_id)
            # Persist the tenant's post-remediation state immediately.  Disable
            # auto-remediation for this verification run so a remediation that
            # does not fully resolve the issue cannot recurse indefinitely.
            refreshed = await run_single_check(
                company_id=company_id,
                check_id=check_id,
                allow_auto_remediation=False,
                previous_status=previous_status,
                emit_ticket_on_fail=False,
            )
            status = refreshed["status"]
            details = refreshed["details"]
            run_at = refreshed["run_at"]
            affected_accounts = refreshed.get("affected_accounts") or []

        await _maybe_create_ticket_on_fail(
            company_id=company_id,
            check_id=check_id,
            check_name=check_name,
            status=status,
            details=details,
            run_at=run_at,
            previous_status=previous_status,
            create_ticket_on_fail_ids=create_ticket_on_fail_ids,
        )

        result = {
            "check_id": check_id,
            "check_name": check_name,
            "status": status,
            "details": details,
            "run_at": run_at,
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
            "has_remediation": bool(bp.get("has_remediation")),
            "affected_accounts": affected_accounts,
            "regression_detected": _is_regression(previous_status, status),
        }
        result.update(_posture_metadata_for_bp(bp))
        results.append(result)

    log_info(
        "M365 best practices run",
        company_id=company_id,
        check_count=len(results),
    )
    return _sort_results_by_priority(results)


async def run_single_check(
    company_id: int,
    check_id: str,
    *,
    allow_auto_remediation: bool = True,
    previous_status: str | None = None,
    emit_ticket_on_fail: bool = True,
) -> dict[str, Any]:
    """Run a single best-practice check by ``check_id`` for ``company_id``.

    Acquires the necessary access tokens, runs only the named check (including
    the self-heal permission grant used by :func:`run_best_practices`), persists
    the result, and returns a result dict in the same shape as the entries
    returned by :func:`run_best_practices`.

    Raises :class:`ValueError` if ``check_id`` is unknown or not currently
    enabled globally.  ``allow_auto_remediation`` is disabled by post-remediation
    verification runs to prevent an unresolved check from remediating recursively.
    Callers that reset stored results before evaluation can pass ``previous_status``
    explicitly so pass→fail ticket detection still uses the pre-reset state.
    """
    catalog = _catalog_map()
    bp = catalog.get(check_id)
    if not bp:
        raise ValueError(f"Unknown best-practice check '{check_id}'")

    enabled = await get_enabled_check_ids()
    if check_id not in enabled:
        raise ValueError(f"Best-practice check '{check_id}' is not enabled")
    create_ticket_on_fail_ids = (
        await get_create_ticket_on_fail_check_ids() if emit_ticket_on_fail else set()
    )
    if previous_status is None and check_id in create_ticket_on_fail_ids:
        previous_status = await bp_repo.get_result_status(company_id, check_id)

    # Keep single-check runs consistent with full runs: execute checks with an
    # app-only token so permission-sensitive checks don't depend on delegated
    # token scopes cached from interactive auth flows.
    graph_token = await acquire_access_token(
        company_id, force_client_credentials=True
    )

    # Self-heal: re-apply any missing app role assignments (mirrors run_best_practices).
    try:
        delegated_token = await acquire_delegated_token(company_id)
    except Exception:  # noqa: BLE001 – self-heal must never raise
        delegated_token = None
    if delegated_token:
        try:
            granted = await try_grant_missing_permissions(
                company_id, access_token=delegated_token
            )
            if granted:
                graph_token = await acquire_access_token(
                    company_id, force_client_credentials=True
                )
        except Exception:  # noqa: BLE001 – self-heal must never raise
            pass

    run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    tenant_capabilities = await detect_tenant_capabilities(graph_token)
    check_name = bp["name"]
    cis_group = bp.get("cis_group")
    affected_accounts: list[dict[str, str]] = []

    missing = _missing_capabilities(bp.get("requires_licenses"), tenant_capabilities)
    if missing:
        status = STATUS_NOT_APPLICABLE
        details = (
            "Not applicable – this check requires the following Microsoft 365 "
            f"license(s) which the tenant does not have: "
            f"{_format_missing_licenses(missing)}."
        )
    elif bp.get("requires_teams_manage_as_app"):
        status = STATUS_NOT_APPLICABLE
        details = _TEAMS_PS_NOT_APPLICABLE_DETAILS
    elif cis_group and cis_group in _CIS_GROUP_RUNNERS:
        batch_runner = _CIS_GROUP_RUNNERS.get(cis_group)
        if batch_runner:
            try:
                batch = await _call_check_with_retry(
                    lambda runner=batch_runner: runner(graph_token),
                    company_id=company_id,
                    check_id=f"cis_group:{cis_group}",
                )
                group_results = {r["check_id"]: r for r in batch}
                raw = group_results.get(check_id)
                if raw:
                    status = raw.get("status", STATUS_UNKNOWN)
                    details = raw.get("details") or ""
                    affected_accounts = raw.get("affected_accounts") or []
                else:
                    status = STATUS_UNKNOWN
                    details = "Check result not available from batch run."
            except M365Error as exc:
                log_error(
                    "CIS Intune benchmark batch failed",
                    company_id=company_id,
                    cis_group=cis_group,
                    error=str(exc),
                )
                status = STATUS_UNKNOWN
                details = f"Unable to evaluate check: {exc}"
        else:
            status = STATUS_UNKNOWN
            details = "No batch runner available for this check group."
    else:
        source_type = bp.get("source_type", "graph")
        runner: BestPracticeRunner = bp["source"]
        try:
            if source_type == "exo":
                exo_token, exo_tenant_id = await _acquire_exo_access_token(company_id)
                if bp.get("uses_company_email_domains"):
                    email_domains = (
                        await companies_repo.get_email_domains_for_company(company_id)
                    )
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(  # type: ignore[call-arg,misc]
                            exo_token, exo_tenant_id, email_domains
                        ),
                        company_id=company_id,
                        check_id=check_id,
                    )
                else:
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(exo_token, exo_tenant_id),  # type: ignore[call-arg,misc]
                        company_id=company_id,
                        check_id=check_id,
                    )
            elif source_type == "scc":
                scc_tok, scc_tid = await _acquire_scc_access_token(company_id)
                raw = await _call_check_with_retry(
                    lambda r=runner: r(scc_tok, scc_tid),  # type: ignore[call-arg,misc]
                    company_id=company_id,
                    check_id=check_id,
                )
            else:
                if bp.get("uses_company_id"):
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(graph_token, company_id),  # type: ignore[call-arg,misc]
                        company_id=company_id,
                        check_id=check_id,
                    )
                elif bp.get("uses_company_email_domains"):
                    email_domains = await companies_repo.get_email_domains_for_company(company_id)
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(graph_token, email_domains),  # type: ignore[call-arg,misc]
                        company_id=company_id,
                        check_id=check_id,
                    )
                else:
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(graph_token),  # type: ignore[call-arg,misc]
                        company_id=company_id,
                        check_id=check_id,
                    )
            status = raw.get("status", STATUS_UNKNOWN)
            details = raw.get("details") or ""
            affected_accounts = raw.get("affected_accounts") or []
        except M365Error as exc:
            log_error(
                "M365 best practice check failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            status = STATUS_UNKNOWN
            details = f"Unable to evaluate check: {exc}"

    if affected_accounts:
        status, details, affected_accounts = await _apply_account_exclusions(
            company_id, check_id, status, details, affected_accounts
        )
    details = _with_regression_notice(
        details,
        previous_status=previous_status,
        status=status,
    )
    await bp_repo.upsert_result(
        company_id=company_id,
        check_id=check_id,
        check_name=check_name,
        status=status,
        details=details,
        affected_accounts=affected_accounts,
        run_at=run_at,
    )

    auto_remediate_ids = (
        await get_auto_remediate_check_ids() if allow_auto_remediation else set()
    )
    if status == STATUS_FAIL and check_id in auto_remediate_ids:
        log_info(
            "M365 best practice auto-remediation triggered",
            company_id=company_id,
            check_id=check_id,
        )
        await remediate_check(company_id=company_id, check_id=check_id)
        return await run_single_check(
            company_id=company_id,
            check_id=check_id,
            allow_auto_remediation=False,
            previous_status=previous_status,
            emit_ticket_on_fail=emit_ticket_on_fail,
        )

    if emit_ticket_on_fail:
        await _maybe_create_ticket_on_fail(
            company_id=company_id,
            check_id=check_id,
            check_name=check_name,
            status=status,
            details=details,
            run_at=run_at,
            previous_status=previous_status,
            create_ticket_on_fail_ids=create_ticket_on_fail_ids,
        )

    log_info(
        "M365 single best practice check run",
        company_id=company_id,
        check_id=check_id,
    )
    result = {
        "check_id": check_id,
        "check_name": check_name,
        "status": status,
        "details": details,
        "run_at": run_at,
        "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
        "has_remediation": bool(bp.get("has_remediation")),
        "affected_accounts": affected_accounts,
        "regression_detected": _is_regression(previous_status, status),
    }
    result.update(_posture_metadata_for_bp(bp))
    return result


async def get_last_results(company_id: int) -> list[dict[str, Any]]:
    """Return the most recent stored best-practice results for ``company_id``.

    Only checks that are currently globally enabled are returned; results for
    disabled checks are filtered out (and are also cleared by
    :func:`set_enabled_checks`).  Each entry is enriched with remediation
    guidance for failed checks and with the catalog metadata.
    """
    rows = await bp_repo.list_results(company_id)
    enabled = await get_enabled_check_ids()
    excluded = await bp_repo.get_company_exclusions(company_id)
    catalog = _catalog_map()

    out: list[dict[str, Any]] = []
    for row in rows:
        check_id = row["check_id"]
        if check_id not in enabled or check_id in excluded:
            continue
        bp_meta = catalog.get(check_id, {"id": check_id})
        status = row.get("status") or STATUS_UNKNOWN
        result = {
            "check_id": check_id,
            "check_name": row.get("check_name") or bp_meta.get("name", check_id),
            "description": bp_meta.get("description", ""),
            "status": status,
            "details": row.get("details") or "",
            "notes": row.get("notes") or "",
            "run_at": row.get("run_at"),
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
            "has_remediation": bool(bp_meta.get("has_remediation")),
            "remediation_status": row.get("remediation_status"),
            "remediated_at": row.get("remediated_at"),
            "remediation_failure_reason": row.get("remediation_failure_reason"),
            "is_cis_benchmark": bool(bp_meta.get("is_cis_benchmark")),
            "cis_group": bp_meta.get("cis_group", ""),
            "affected_accounts": row.get("affected_accounts") or [],
        }
        result.update(_posture_metadata_for_bp(bp_meta))
        result["regression_detected"] = str(result.get("details") or "").startswith(
            _REGRESSION_NOTICE
        )
        out.append(result)
    return _sort_results_by_priority(out)


def _normalise_batch_scope(scope: str | None) -> str | None:
    value = str(scope or "").strip().lower()
    return value if value in _BATCH_REMEDIATION_SCOPES else None


async def remediate_failed_checks_batch(company_id: int, *, scope: str) -> dict[str, Any]:
    normalised_scope = _normalise_batch_scope(scope)
    if normalised_scope is None:
        raise ValueError("Invalid remediation batch scope")
    results = await get_last_results(company_id)
    candidates = [
        result
        for result in results
        if str(result.get("batch_scope") or "m365") == normalised_scope
        and result.get("status") == STATUS_FAIL
        and result.get("has_remediation")
    ]
    if not candidates:
        return {
            "success": True,
            "message": f"No failed remediations are pending in {_BATCH_REMEDIATION_SCOPES[normalised_scope]}.",
            "scope": normalised_scope,
            "scope_label": _BATCH_REMEDIATION_SCOPES[normalised_scope],
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "failures": [],
        }
    failures: list[str] = []
    refresh_issues: list[str] = []
    succeeded = 0
    for candidate in candidates:
        check_id = str(candidate.get("check_id") or "")
        try:
            outcome = await remediate_check(company_id=company_id, check_id=check_id)
        except (ValueError, M365Error) as exc:
            failures.append(f"{candidate.get('check_name') or check_id}: remediation failed ({exc})")
            continue
        remediation_succeeded = bool(outcome.get("success"))
        try:
            await run_single_check(
                company_id=company_id,
                check_id=check_id,
                allow_auto_remediation=False,
                previous_status=STATUS_FAIL,
                emit_ticket_on_fail=False,
            )
        except (ValueError, M365Error) as exc:
            if remediation_succeeded:
                succeeded += 1
                refresh_issues.append(
                    f"{candidate.get('check_name') or check_id}: unable to refresh check ({exc})"
                )
            else:
                failures.append(
                    f"{candidate.get('check_name') or check_id}: unable to refresh check ({exc})"
                )
            continue
        if remediation_succeeded:
            succeeded += 1
        else:
            failures.append(f"{candidate.get('check_name') or check_id}: {outcome.get('message') or 'Remediation failed'}")
    failed = len(failures)
    scope_label = _BATCH_REMEDIATION_SCOPES[normalised_scope]
    message = (
        f"Batch remediation finished for {scope_label}: {succeeded} succeeded, {failed} failed."
    )
    if failed:
        message = f"{message} Review per-check remediation status below for details."
    elif refresh_issues:
        message = (
            f"{message} Verification warnings were recorded for {len(refresh_issues)} check(s); "
            "review the latest evaluation details below."
        )
    log_info(
        "M365 best practice batch remediation finished",
        company_id=company_id,
        scope=normalised_scope,
        total=len(candidates),
        succeeded=succeeded,
        failed=failed,
        refresh_warnings=len(refresh_issues),
    )
    return {
        "success": failed == 0 and not refresh_issues,
        "message": message,
        "scope": normalised_scope,
        "scope_label": scope_label,
        "total": len(candidates),
        "succeeded": succeeded,
        "failed": failed,
        "failures": failures,
        "refresh_issues": refresh_issues,
    }


def get_secure_score_summary(results: list[dict[str, Any]]) -> dict[str, float] | None:
    """Return the latest Microsoft Secure Score values from evaluated results."""
    secure_score_result = next(
        (
            result
            for result in results
            if result.get("check_id") == "bp_monitor_secure_score"
        ),
        None,
    )
    if not secure_score_result:
        return None

    current, maximum, percentage = bp_repo._parse_secure_score(
        secure_score_result.get("details")
    )
    if current is None or maximum is None or percentage is None:
        return None
    return {"current": current, "maximum": maximum, "percentage": percentage}


async def get_daily_history(company_id: int) -> list[dict[str, Any]]:
    """Return the company's daily best-practice and Secure Score snapshots."""
    return await bp_repo.list_daily_history(company_id)


async def set_account_exclusion(
    *, company_id: int, check_id: str, account_id: str, account_name: str, excluded: bool
) -> None:
    """Persist an account exclusion after the route validates the current finding."""
    await bp_repo.set_account_exclusion(
        company_id=company_id, check_id=check_id, account_id=account_id,
        account_name=account_name, excluded=excluded,
    )


async def set_result_notes(*, company_id: int, check_id: str, notes: str | None) -> bool:
    """Persist the per-check technician/admin note for a company result."""
    return await bp_repo.update_result_notes(
        company_id=company_id,
        check_id=check_id,
        notes=notes,
    )


async def _remediate_ews_dependency_allow_list(
    graph_token: str, company_id: int
) -> tuple[bool, str]:
    state = await _collect_ews_dependency_state(graph_token, company_id)
    required_apps = state["required_apps"]
    if not required_apps:
        return (
            False,
            "No confirmed EWS dependency was found. Review observed usage and add any "
            "approved infrequent AppIDs to the check notes before enabling EWS.",
        )

    exo_token = str(state["exo_token"])
    tenant_id = str(state["tenant_id"])
    current_allowed = list(state["current_allowed"])
    required_ids = [app["app_id"] for app in required_apps]
    merged_allowed = list(current_allowed)
    merged_seen = set(current_allowed)
    for app_id in required_ids:
        if app_id not in merged_seen:
            merged_seen.add(app_id)
            merged_allowed.append(app_id)

    missing_required = [
        app_id for app_id in required_ids if app_id not in set(current_allowed)
    ]
    params: dict[str, Any] = {}
    if state["ews_enabled"] is not True:
        params["EwsEnabled"] = True
    if missing_required:
        params["EwsAllowedAppIDs"] = merged_allowed

    if not params:
        return (
            True,
            "EWS is already enabled for the confirmed required applications. "
            "No remediation changes were needed.",
        )

    await _exo_invoke_command(exo_token, tenant_id, "Set-OrganizationConfig", params)
    verified = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
    verified_cfg = _exo_first_value(verified)
    verified_allowed = set(_extract_app_ids(verified_cfg.get("EwsAllowedAppIDs")))
    missing_after = [
        app_id for app_id in required_ids if app_id not in verified_allowed
    ]
    if verified_cfg.get("EwsEnabled") is not True or missing_after:
        return (
            False,
            "EWS remediation was submitted, but the updated configuration was not yet "
            "fully visible when re-read. Wait a few minutes for Exchange Online "
            "propagation, test the approved apps, then re-run the check.",
        )

    return (
        True,
        "Enabled EWS for the tenant and preserved the existing EwsAllowedAppIDs "
        "while adding the confirmed required AppIDs. Test the approved applications "
        "again after Exchange Online propagation completes.",
    )


async def _remediate_foreach_mailbox(
    exo_token: str,
    tenant_id: str,
    company_id: int,
    check_id: str,
    mailbox_params: dict[str, Any],
) -> bool:
    """Enable auditing (or apply other per-mailbox settings) on all user mailboxes.

    Fetches every user mailbox, then calls ``Set-Mailbox`` for each one that
    does not already satisfy every key/value pair in *mailbox_params*.
    Multi-value ``{"Add": [...]}`` parameters are reduced to values missing
    from the mailbox so remediation is safe to retry.
    Returns ``True`` if all required updates succeeded (or none were needed),
    ``False`` if at least one update failed.
    """
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-Mailbox",
            {"ResultSize": "Unlimited", "Filter": "RecipientTypeDetails -eq 'UserMailbox'"},
        )
    except M365Error as exc:
        log_error(
            "M365 foreach-mailbox remediation – Get-Mailbox failed",
            company_id=company_id,
            check_id=check_id,
            error=str(exc),
        )
        return False

    rows = data.get("value") or []
    all_ok = True
    for mailbox in rows:
        if not isinstance(mailbox, dict):
            continue
        identity = mailbox.get("UserPrincipalName") or mailbox.get("Identity")
        if not identity:
            continue
        update_params: dict[str, Any] = {}
        for key, desired in mailbox_params.items():
            if isinstance(desired, dict) and set(desired) == {"Add"}:
                existing = set(mailbox.get(key) or [])
                missing = [value for value in desired["Add"] if value not in existing]
                if missing:
                    # The EXO REST API does not support the PowerShell hash-table
                    # @{Add=...} syntax for array parameters.  Pass the full merged
                    # list of values so the cmdlet receives a plain JSON array.
                    update_params[key] = list(existing | set(desired["Add"]))
            elif mailbox.get(key) != desired:
                update_params[key] = desired

        if not update_params:
            continue
        try:
            await _exo_invoke_command(
                exo_token, tenant_id, "Set-Mailbox",
                {"Identity": identity, **update_params},
            )
        except M365Error as exc:
            log_error(
                "M365 foreach-mailbox remediation – Set-Mailbox failed",
                company_id=company_id,
                check_id=check_id,
                identity=identity,
                error=str(exc),
            )
            all_ok = False
    return all_ok


async def _remediate_foreach_owa_mailbox_policy(
    exo_token: str,
    tenant_id: str,
    company_id: int,
    check_id: str,
    policy_params: dict[str, Any],
) -> bool:
    """Apply the requested settings to every OWA mailbox policy.

    Policies that already have the desired values are skipped, making the
    operation safe to retry.  A failure on one policy does not prevent the
    remaining policies from being remediated.
    """
    try:
        data = await _exo_invoke_command(
            exo_token, tenant_id, "Get-OwaMailboxPolicy"
        )
    except M365Error as exc:
        log_error(
            "M365 foreach-OWA-policy remediation – Get-OwaMailboxPolicy failed",
            company_id=company_id,
            check_id=check_id,
            error=str(exc),
        )
        return False

    all_ok = True
    for policy in data.get("value") or []:
        if not isinstance(policy, dict):
            continue
        identity = policy.get("Identity") or policy.get("Name")
        already_compliant = all(
            policy.get(key) == value for key, value in policy_params.items()
        )
        if not identity or already_compliant:
            continue
        try:
            await _exo_invoke_command(
                exo_token,
                tenant_id,
                "Set-OwaMailboxPolicy",
                {"Identity": identity, **policy_params},
            )
        except M365Error as exc:
            log_error(
                "M365 foreach-OWA-policy remediation – Set-OwaMailboxPolicy failed",
                company_id=company_id,
                check_id=check_id,
                identity=identity,
                error=str(exc),
            )
            all_ok = False
    return all_ok


def _antiphish_policy_matches_remediation(check_id: str, row: dict[str, Any]) -> bool:
    """Return whether an anti-phish policy is a valid remediation target."""
    if check_id == "bp_antiphish_domain_impersonation_safety_tip":
        return _coerce_exo_bool(row.get("EnableOrganizationDomainsProtection")) or _coerce_exo_bool(
            row.get("EnableTargetedDomainsProtection")
        )
    if check_id == "bp_antiphish_user_impersonation_safety_tip":
        return _coerce_exo_bool(row.get("EnableTargetedUserProtection"))
    return False


def _antiphish_remediation_prerequisite_message(check_id: str) -> str:
    """Return an actionable failure message for unsupported anti-phish automation."""
    if check_id == "bp_antiphish_domain_impersonation_safety_tip":
        return (
            "Automated remediation requires an anti-phishing policy with domain impersonation "
            "protection enabled. Configure organization-domain or targeted-domain protection "
            "first, then retry."
        )
    if check_id == "bp_antiphish_user_impersonation_safety_tip":
        return (
            "Automated remediation requires an anti-phishing policy with user impersonation "
            "protection enabled. Configure targeted-user protection first, then retry."
        )
    return "Automated remediation is not available for this anti-phishing policy state."


_DEFAULT_ANTIPHISH_POLICY = "Office365 AntiPhish Default"


async def _enable_domain_impersonation_protection(
    exo_token: str,
    tenant_id: str,
) -> None:
    """Enable organization-domain impersonation protection on the default anti-phish policy."""
    await _exo_invoke_command(
        exo_token,
        tenant_id,
        "Set-AntiPhishPolicy",
        {
            "Identity": _DEFAULT_ANTIPHISH_POLICY,
            "EnableOrganizationDomainsProtection": True,
            "Confirm": False,
        },
    )


async def _remediate_matching_antiphish_policies(
    exo_token: str,
    tenant_id: str,
    check_id: str,
    cmdlet: str,
    base_params: dict[str, Any],
) -> tuple[bool, str]:
    """Apply an anti-phish remediation to each matching policy.

    For domain-impersonation safety-tip remediations, if no policy currently
    has domain impersonation protection enabled, the default policy
    ("Office365 AntiPhish Default") is automatically configured with
    EnableOrganizationDomainsProtection before the safety-tip setting is applied.
    """
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-AntiPhishPolicy")
    except M365Error as exc:
        return False, f"Unable to query Get-AntiPhishPolicy: {exc}"

    targets: list[str] = []
    for row in data.get("value") or []:
        if not isinstance(row, dict) or not _antiphish_policy_matches_remediation(check_id, row):
            continue
        identity = str(row.get("Identity") or row.get("Name") or "").strip()
        if identity and identity not in targets:
            targets.append(identity)

    if not targets:
        if check_id == "bp_antiphish_domain_impersonation_safety_tip":
            await _enable_domain_impersonation_protection(exo_token, tenant_id)
            targets.append(_DEFAULT_ANTIPHISH_POLICY)
        else:
            return False, _antiphish_remediation_prerequisite_message(check_id)

    for identity in targets:
        params = dict(base_params)
        params["Identity"] = identity
        try:
            await _exo_invoke_command(exo_token, tenant_id, cmdlet, params)
        except M365Error as exc:
            return False, str(exc)
    return True, ""


async def _remediate_global_quarantine_policy(
    exo_token: str,
    tenant_id: str,
    cmdlet: str,
    base_params: dict[str, Any],
) -> tuple[bool, str]:
    """Apply remediation to the tenant's current global quarantine policy identity."""
    try:
        data = await _exo_invoke_command(
            exo_token,
            tenant_id,
            "Get-QuarantinePolicy",
        )
    except M365Error as exc:
        return False, f"Unable to query Get-QuarantinePolicy: {exc}"

    rows = data.get("value") or []
    policy = _select_global_quarantine_policy(rows)
    if not policy:
        return False, "Unable to determine the global quarantine policy identity."
    identity = str(policy.get("Identity") or policy.get("Name") or "").strip()
    if not identity:
        return False, "Unable to determine the global quarantine policy identity."

    params = dict(base_params)
    params["Identity"] = identity
    try:
        await _exo_invoke_command(exo_token, tenant_id, cmdlet, params)
    except M365Error as exc:
        return False, str(exc)
    return True, ""


async def _remediate_foreach_user_graph(
    graph_token: str,
    company_id: int,
    check_id: str,
) -> bool:
    """Disable sign-in for all unlicensed member accounts (shared mailboxes).

    Fetches all users via Microsoft Graph, then PATCHes each unlicensed member
    account that currently has ``accountEnabled=True`` to ``{"accountEnabled": false}``.
    On-premises-synced accounts are skipped because Azure AD does not permit
    modifying cloud-managed attributes on objects synced from on-prem AD.
    Returns ``True`` if all required updates succeeded (or none were needed),
    ``False`` if at least one update failed.
    """
    users = await _safe_graph_get_all(graph_token, _USERS_LIST_URL)
    if users is None:
        log_error(
            "M365 foreach-user-graph remediation – unable to enumerate users",
            company_id=company_id,
            check_id=check_id,
        )
        return False

    admin_ids = await _get_directory_role_member_ids(graph_token)
    if admin_ids is None:
        log_error(
            "M365 foreach-user-graph remediation – unable to enumerate administrator role members",
            company_id=company_id,
            check_id=check_id,
        )
        return False

    candidates = [
        u for u in users
        if (u.get("userType") or "").lower() == "member"
        and not (u.get("assignedLicenses") or [])
        and u.get("accountEnabled") is True
        and str(u.get("id") or "") not in admin_ids
        and not u.get("onPremisesSyncEnabled")  # can't disable sign-in for on-prem-synced accounts via Graph
    ]
    all_ok = True
    for user in candidates:
        user_id = user.get("id")
        if not user_id:
            continue
        user_url = f"https://graph.microsoft.com/v1.0/users/{user_id}"
        try:
            await _graph_patch(graph_token, user_url, {"accountEnabled": False})
        except M365Error as exc:
            log_error(
                "M365 foreach-user-graph remediation – PATCH user failed",
                company_id=company_id,
                check_id=check_id,
                user_id=user_id,
                error=str(exc),
            )
            all_ok = False
    return all_ok


async def _remediate_disable_per_user_mfa(
    graph_token: str, company_id: int, check_id: str
) -> tuple[bool, str]:
    """Disable per-user MFA for enabled users when Conditional Access is available."""
    policies = await _safe_graph_get_all(graph_token, _CA_POLICIES_URL)
    if policies is None:
        return False, "Unable to enumerate Conditional Access policies."

    has_configured_ca = any(
        str(policy.get("state") or "").strip().lower() in _ACTIVE_CONDITIONAL_ACCESS_POLICY_STATES_LOWER
        for policy in policies
    )
    if not has_configured_ca:
        return (
            False,
            "No active Conditional Access policy found. Configure Conditional Access before disabling per-user MFA.",
        )

    users = await _safe_graph_get_all(graph_token, _USERS_LIST_URL)
    if users is None:
        return False, "Unable to enumerate users for per-user MFA remediation."

    all_ok = True
    for user in users:
        user_id = str(user.get("id") or "").strip()
        if not user_id or not user.get("accountEnabled", False):
            continue
        requirement_url = _AUTHENTICATION_REQUIREMENTS_URL_TMPL.format(user_id=user_id)
        data = await _safe_graph_get(graph_token, requirement_url)
        if data is None:
            log_error(
                "M365 per-user MFA remediation – requirements lookup failed",
                company_id=company_id,
                check_id=check_id,
                user_id=user_id,
            )
            all_ok = False
            continue

        state = str(data.get("perUserMfaState") or "").lower()
        if not state or state == "disabled":
            continue
        try:
            await _graph_patch(
                graph_token,
                requirement_url,
                {"perUserMfaState": "disabled"},
            )
        except M365Error as exc:
            log_error(
                "M365 per-user MFA remediation – update failed",
                company_id=company_id,
                check_id=check_id,
                user_id=user_id,
                error=str(exc),
            )
            all_ok = False

    if not all_ok:
        return False, "One or more per-user MFA settings could not be disabled."
    return True, ""


async def _remediate_create_dynamic_guest_group(graph_token: str) -> bool:
    """Create the standard dynamic security group that contains every guest.

    Re-check immediately before creating the group so repeated or concurrent
    remediation requests do not intentionally create duplicate groups.
    """
    current = await _check_dynamic_group_for_guests(graph_token)
    if current["status"] == STATUS_PASS:
        return True
    if current["status"] == STATUS_UNKNOWN:
        return False

    await _graph_post(
        graph_token,
        _GROUPS_LIST_URL,
        {
            "displayName": "Guest Users",
            "description": "Dynamic security group containing all guest users.",
            "groupTypes": ["DynamicMembership"],
            "mailEnabled": False,
            "mailNickname": "GuestUsers",
            "membershipRule": '(user.userType -eq "Guest")',
            "membershipRuleProcessingState": "On",
            "securityEnabled": True,
        },
    )
    return True


async def _remediate_foreach_public_group_graph(
    graph_token: str, company_id: int, check_id: str
) -> bool:
    """Convert every non-excluded public Microsoft 365 group to Private."""
    groups = await _safe_graph_get_all(graph_token, _GROUPS_LIST_URL)
    if groups is None:
        log_error(
            "M365 foreach-public-group remediation – list groups failed",
            company_id=company_id,
            check_id=check_id,
        )
        return False

    try:
        exclusions = await bp_repo.get_account_exclusions(company_id, check_id)
    except Exception as exc:  # noqa: BLE001 - exclusion lookup should fail closed
        log_error(
            "M365 foreach-public-group remediation – exclusion lookup failed",
            company_id=company_id,
            check_id=check_id,
            error=str(exc),
        )
        return False
    excluded_ids = {group_id for _, group_id in exclusions}

    all_ok = True
    for group in groups:
        group_id = str(group.get("id") or "").strip()
        if not group_id:
            continue
        if str(group.get("visibility") or "").lower() != "public":
            continue
        if "Unified" not in (group.get("groupTypes") or []):
            continue
        if group_id in excluded_ids:
            continue
        group_url = _GROUP_URL_TMPL.format(group_id=group_id)
        try:
            await _graph_patch(graph_token, group_url, {"visibility": "Private"})
        except M365Error as exc:
            log_error(
                "M365 foreach-public-group remediation – PATCH group failed",
                company_id=company_id,
                check_id=check_id,
                group_id=group_id,
                error=str(exc),
            )
            all_ok = False
    return all_ok


async def remediate_check(company_id: int, check_id: str) -> dict[str, Any]:
    """Attempt automated remediation for a single best-practice check.

    Looks up the remediation command from the catalog, executes it via the
    Exchange Online REST API (for EXO-type checks), records the outcome in the
    database, and returns a result dict with ``success`` (bool) and ``message``
    (str) keys.

    Supports nine remediation patterns:

    * ``source_type="exo"`` – executes a single cmdlet via the Exchange Online
      REST API using the ``remediation_cmdlet`` and ``remediation_params``
      catalog fields.
    * ``source_type="exo"`` with ``remediation_type="foreach_mailbox_exo"`` –
      fetches all user mailboxes and calls ``Set-Mailbox`` on each one that does
      not already satisfy the required parameters using the
      ``remediation_mailbox_params`` catalog field.
    * ``source_type="exo"`` with
      ``remediation_type="foreach_owa_mailbox_policy_exo"`` – fetches every
      OWA mailbox policy and applies the catalog's remediation parameters to
      each policy that does not already satisfy them.
    * ``source_type="graph"`` – issues a ``PATCH`` request to Microsoft Graph
      using the ``remediation_url`` and ``remediation_payload`` catalog fields.
    * ``source_type="graph"`` with ``remediation_type="foreach_user_graph"`` –
      fetches all users and disables sign-in for each unlicensed member account
      that currently has ``accountEnabled=True``.
    * ``source_type="graph"`` with ``remediation_type="disable_per_user_mfa"`` –
      disables legacy per-user MFA states after confirming at least one enabled
      Conditional Access policy exists.
    * ``source_type="graph"`` with
      ``remediation_type="create_dynamic_guest_group"`` – creates the standard
      ``Guest Users`` dynamic security group if one does not already exist.
    * ``source_type="graph"`` with
      ``remediation_type="foreach_public_group_graph"`` – converts each
      non-excluded public Microsoft 365 group to ``visibility=Private``.
    * ``source_type="scc"`` with ``remediation_type="break_glass_alert_policy"`` –
      creates (or re-enables) a Microsoft Purview protection alert policy that
      emails tenant admins whenever a MyPortal-managed break-glass account signs in.
    """
    bp = _catalog_map().get(check_id)
    if not bp or not bp.get("has_remediation"):
        return {
            "success": False,
            "message": "Automated remediation is not available for this check.",
        }

    source_type = bp.get("source_type", "graph")
    remediated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    generic_failure_reason = "Check that the app has the required permissions."
    outcome_message = ""

    if bp.get("remediation_type") == "renew_myportal_pkce_admin_secret":
        target = await _resolve_myportal_pkce_credential_target(company_id)
        if not target:
            outcome_message = "MyPortal PKCE/bootstrap admin credentials are not configured."
            success = False
        elif target["scope"] == "environment":
            outcome_message = (
                "MyPortal PKCE/bootstrap credentials are configured from environment variables, "
                "so MyPortal cannot persist a rotated secret automatically. Store managed admin "
                "credentials in MyPortal and retry."
            )
            success = False
        else:
            try:
                renewal_result = await renew_admin_client_secret(
                    company_id if target["scope"] == "company" else None
                )
                had_previous_key = bool(renewal_result.get("had_previous_key", False))
                revoked_previous = bool(renewal_result.get("revoked_previous", False))
                expires_at = renewal_result.get("expires_at")
                expires_text = (
                    expires_at.date().isoformat()
                    if isinstance(expires_at, datetime)
                    else "the configured lifetime window"
                )
                if not had_previous_key or revoked_previous:
                    success = True
                    outcome_message = (
                        "Rotated the MyPortal PKCE/bootstrap credential and validated the replacement. "
                        f"The new credential expires on {expires_text}."
                    )
                else:
                    success = False
                    outcome_message = (
                        "MyPortal validated and activated a replacement PKCE/bootstrap credential, "
                        "but could not retire the previous expiring credential. Authentication should "
                        "continue to work; retry remediation after reviewing the logged Graph error."
                    )
            except M365Error as exc:
                success = False
                outcome_message = str(exc)

        remediation_status = "success" if success else "failed"
        remediation_failure_reason = None if success else outcome_message
        await bp_repo.update_remediation_status(
            company_id=company_id,
            check_id=check_id,
            remediation_status=remediation_status,
            remediated_at=remediated_at,
            remediation_failure_reason=remediation_failure_reason,
        )
        return {"success": success, "message": outcome_message}

    if source_type == "exo":
        token_error_message = (
            "Unable to acquire Exchange Online token. "
            "Check that the app credentials are correct."
        )
        try:
            exo_token, tenant_id = await _acquire_exo_access_token(company_id)
        except M365Error as exc:
            log_error(
                "M365 best practice remediation – EXO token acquisition failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            await bp_repo.update_remediation_status(
                company_id=company_id,
                check_id=check_id,
                remediation_status="failed",
                remediated_at=remediated_at,
                remediation_failure_reason=token_error_message,
            )
            return {
                "success": False,
                "message": token_error_message,
            }

        if bp.get("remediation_type") == "foreach_mailbox_exo":
            mailbox_params = bp.get("remediation_mailbox_params") or {}
            success = await _remediate_foreach_mailbox(
                exo_token, tenant_id, company_id, check_id, mailbox_params
            )
            if not success:
                outcome_message = "One or more mailbox remediation updates failed."
        elif bp.get("remediation_type") == "it_contact_baseline_exo":
            try:
                success, outcome_message = await _remediate_it_contact_baseline(
                    exo_token, tenant_id
                )
            except M365Error as exc:
                success = False
                outcome_message = str(exc)
        elif bp.get("remediation_type") == "foreach_owa_mailbox_policy_exo":
            params = bp.get("remediation_params") or {}
            success = await _remediate_foreach_owa_mailbox_policy(
                exo_token, tenant_id, company_id, check_id, params
            )
            if not success:
                outcome_message = "One or more OWA mailbox policy remediation updates failed."
        elif bp.get("remediation_type") == "matching_antiphish_policy_exo":
            cmdlet = bp.get("remediation_cmdlet", "")
            params = bp.get("remediation_params") or {}
            try:
                success, outcome_message = await _remediate_matching_antiphish_policies(
                    exo_token, tenant_id, check_id, cmdlet, params
                )
            except M365Error as exc:
                outcome_message = str(exc)
                log_error(
                    "M365 best practice remediation command failed",
                    company_id=company_id,
                    check_id=check_id,
                    cmdlet=cmdlet,
                    error=str(exc),
                )
                success = False
        elif bp.get("remediation_type") == "global_quarantine_policy_exo":
            cmdlet = bp.get("remediation_cmdlet", "")
            params = bp.get("remediation_params") or {}
            success, outcome_message = await _remediate_global_quarantine_policy(
                exo_token, tenant_id, cmdlet, params
            )
        else:
            cmdlet = bp.get("remediation_cmdlet", "")
            params = bp.get("remediation_params") or {}
            try:
                await _exo_invoke_command(exo_token, tenant_id, cmdlet, params)
                success = True
            except M365Error as exc:
                # Older enterprise-app registrations can be missing a newly
                # required EXO application or directory role.  In particular,
                # Customer Lockbox needs Compliance Administrator in addition
                # to Exchange.ManageAsApp.  Repair permissions with the stored
                # delegated admin token, then obtain a new EXO token and retry
                # exactly once.  Never retry other failures or retry without a
                # confirmed grant, which avoids masking licensing and policy
                # errors as permission problems.
                granted = False
                if exc.http_status == 403:
                    try:
                        delegated_token = await acquire_delegated_token(company_id)
                        if delegated_token:
                            granted = await try_grant_missing_permissions(
                                company_id, access_token=delegated_token
                            )
                    except Exception as grant_exc:  # noqa: BLE001 – preserve original EXO error
                        log_error(
                            "M365 best practice remediation permission repair failed",
                            company_id=company_id,
                            check_id=check_id,
                            error=str(grant_exc),
                        )

                if granted:
                    try:
                        exo_token, tenant_id = await _acquire_exo_access_token(company_id)
                        await _exo_invoke_command(exo_token, tenant_id, cmdlet, params)
                        success = True
                    except M365Error as retry_exc:
                        exc = retry_exc
                        success = False
                else:
                    success = False

                if not success:
                    outcome_message = str(exc)
                    log_error(
                        "M365 best practice remediation command failed",
                        company_id=company_id,
                        check_id=check_id,
                        cmdlet=cmdlet,
                        error=str(exc),
                    )
    elif source_type == "graph":
        graph_token_error_message = (
            "Unable to acquire Microsoft Graph token. "
            "Check that the app credentials are correct."
        )
        try:
            # Use an app-only (client credentials) token so that application
            # permissions such as SharePointTenantSettings.ReadWrite.All are
            # present in the token.  A delegated token obtained from a stored
            # refresh token only carries the scopes consented during the
            # interactive connect flow (e.g. AppRoleAssignment.ReadWrite.All)
            # and would be missing the application permissions required for
            # write-based remediations, causing a 403 even after re-consent.
            graph_token = await acquire_access_token(
                company_id, force_client_credentials=True
            )
        except M365Error as exc:
            log_error(
                "M365 best practice remediation – Graph token acquisition failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            await bp_repo.update_remediation_status(
                company_id=company_id,
                check_id=check_id,
                remediation_status="failed",
                remediated_at=remediated_at,
                remediation_failure_reason=graph_token_error_message,
            )
            return {
                "success": False,
                "message": graph_token_error_message,
            }
        if bp.get("remediation_type") == "ews_dependency_allow_list":
            try:
                success, outcome_message = await _remediate_ews_dependency_allow_list(
                    graph_token, company_id
                )
            except M365Error as exc:
                log_error(
                    "M365 EWS dependency remediation failed",
                    company_id=company_id,
                    check_id=check_id,
                    error=str(exc),
                )
                success = False
                outcome_message = str(exc)
        elif bp.get("remediation_type") == "global_admin_accounts":
            try:
                success, outcome_message = await _remediate_global_admin_count(graph_token, company_id)
            except Exception as exc:
                log_error("M365 Global Administrator remediation failed", company_id=company_id,
                          check_id=check_id, error=str(exc))
                success = False
                outcome_message = ("Account creation or secure Hudu synchronization failed; "
                                   "the affected new account was rolled back.")
        elif bp.get("remediation_type") == "disable_per_user_mfa":
            success, outcome_message = await _remediate_disable_per_user_mfa(
                graph_token, company_id, check_id
            )
        elif bp.get("remediation_type") == "foreach_user_graph":
            success = await _remediate_foreach_user_graph(graph_token, company_id, check_id)
            if not success:
                outcome_message = "One or more user remediation updates failed."
        elif bp.get("remediation_type") == "create_dynamic_guest_group":
            try:
                success = await _remediate_create_dynamic_guest_group(graph_token)
            except M365Error as exc:
                granted = False
                if exc.http_status == 403:
                    try:
                        delegated_token = await acquire_delegated_token(company_id)
                        if delegated_token:
                            granted = await try_grant_missing_permissions(
                                company_id, access_token=delegated_token
                            )
                    except Exception as grant_exc:  # noqa: BLE001 – preserve original Graph error
                        log_error(
                            "M365 best practice Graph remediation permission repair failed",
                            company_id=company_id,
                            check_id=check_id,
                            error=str(grant_exc),
                        )

                if granted:
                    try:
                        graph_token = await acquire_access_token(
                            company_id, force_client_credentials=True
                        )
                        success = await _remediate_create_dynamic_guest_group(graph_token)
                    except Exception as retry_exc:  # noqa: BLE001 – normalize retry errors into remediation failure
                        exc = (
                            retry_exc
                            if isinstance(retry_exc, M365Error)
                            else M365Error(str(retry_exc))
                        )
                        success = False
                else:
                    success = False

                if not success:
                    log_error(
                        "M365 best practice dynamic guest group remediation failed",
                        company_id=company_id,
                        check_id=check_id,
                        error=str(exc),
                    )
                    outcome_message = str(exc)
            if not success and not outcome_message:
                outcome_message = "Unable to confirm whether the guest group remediation succeeded."
        elif bp.get("remediation_type") == "foreach_public_group_graph":
            success = await _remediate_foreach_public_group_graph(
                graph_token, company_id, check_id
            )
            if not success:
                outcome_message = "One or more public group remediation updates failed."
            # Remediation status is persisted by the shared epilogue below.
        elif check_id == "bp_authenticator_mfa_fatigue":
            try:
                success, outcome_message = await _remediate_authenticator_mfa_fatigue(
                    graph_token
                )
            except M365Error as exc:
                log_error(
                    "M365 Authenticator MFA-fatigue remediation failed",
                    company_id=company_id,
                    check_id=check_id,
                    error=str(exc),
                )
                success = False
                outcome_message = str(exc)
        elif check_id == "bp_internal_phishing_forms":
            try:
                success, outcome_message = await _remediate_internal_phishing_forms(
                    graph_token
                )
            except M365Error as exc:
                granted = False
                success = False
                outcome_message = (
                    _forms_permission_guidance("update Microsoft Forms settings")
                    if exc.http_status == 403
                    else f"Microsoft Graph failed to update Microsoft Forms settings: {exc}"
                )
                permission_repair_error = ""
                if exc.http_status == 403:
                    try:
                        delegated_token = await acquire_delegated_token(company_id)
                        if delegated_token:
                            granted = await try_grant_missing_permissions(
                                company_id, access_token=delegated_token
                            )
                    except Exception as grant_exc:  # noqa: BLE001 – preserve original Graph error
                        permission_repair_error = str(grant_exc)
                        log_error(
                            "M365 best practice Forms remediation permission repair failed",
                            company_id=company_id,
                            check_id=check_id,
                            error=permission_repair_error,
                        )
                if granted:
                    try:
                        graph_token = await acquire_access_token(
                            company_id, force_client_credentials=True
                        )
                        success, outcome_message = await _remediate_internal_phishing_forms(
                            graph_token
                        )
                    except Exception as retry_exc:  # noqa: BLE001 – normalize retry errors into remediation failure
                        success = False
                        if (
                            isinstance(retry_exc, M365Error)
                            and retry_exc.http_status == 403
                        ):
                            outcome_message = _forms_permission_guidance(
                                "update Microsoft Forms settings"
                            )
                        else:
                            outcome_message = str(retry_exc)
                        log_error(
                            "M365 internal phishing Forms remediation failed after permission repair",
                            company_id=company_id,
                            check_id=check_id,
                            error=outcome_message,
                        )
                else:
                    if exc.http_status == 403 and permission_repair_error:
                        outcome_message = (
                            f"{outcome_message} Automatic permission repair also failed: "
                            f"{permission_repair_error}"
                        )
                if not success:
                    log_error(
                        "M365 internal phishing Forms remediation failed",
                        company_id=company_id,
                        check_id=check_id,
                        error=outcome_message,
                    )
        elif check_id == "bp_weak_auth_methods_disabled":
            try:
                success, outcome_message = await _remediate_weak_auth_methods_disabled(
                    graph_token
                )
            except M365Error as exc:
                log_error(
                    "M365 weak authentication methods remediation failed",
                    company_id=company_id,
                    check_id=check_id,
                    error=str(exc),
                )
                success = False
                outcome_message = str(exc)
        else:
            remediation_url = bp.get("remediation_url", "")
            remediation_payload = bp.get("remediation_payload") or {}
            try:
                await _graph_patch(graph_token, remediation_url, remediation_payload)
                success = True
            except M365Error as exc:
                log_error(
                    "M365 best practice Graph remediation failed",
                    company_id=company_id,
                    check_id=check_id,
                    url=remediation_url,
                    error=str(exc),
                )
                success = False
                outcome_message = str(exc)
    elif source_type == "scc":
        scc_token_error_message = (
            "Unable to acquire Security & Compliance token. "
            "Check that the app credentials and permissions are correct."
        )
        try:
            scc_tok, scc_tid = await _acquire_scc_access_token(company_id)
        except M365Error as exc:
            log_error(
                "M365 best practice remediation – SCC token acquisition failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            await bp_repo.update_remediation_status(
                company_id=company_id,
                check_id=check_id,
                remediation_status="failed",
                remediated_at=remediated_at,
                remediation_failure_reason=scc_token_error_message,
            )
            return {
                "success": False,
                "message": scc_token_error_message,
            }
        if bp.get("remediation_type") == "break_glass_alert_policy":
            try:
                graph_token_scc = await acquire_access_token(
                    company_id, force_client_credentials=True
                )
                success, outcome_message = await _remediate_break_glass_alert_policy(
                    graph_token_scc, scc_tok, scc_tid
                )
            except Exception as exc:
                log_error(
                    "M365 break-glass alert policy remediation failed",
                    company_id=company_id,
                    check_id=check_id,
                    error=str(exc),
                )
                success = False
                outcome_message = str(exc)
        else:
            success = False
            outcome_message = "Unknown SCC remediation type."
    else:
        success = False
        outcome_message = "Unknown remediation source type."

    remediation_status = "success" if success else "failed"
    remediation_failure_reason = (
        None if remediation_status == "success"
        else outcome_message or generic_failure_reason
    )
    await bp_repo.update_remediation_status(
        company_id=company_id,
        check_id=check_id,
        remediation_status=remediation_status,
        remediated_at=remediated_at,
        remediation_failure_reason=remediation_failure_reason,
    )

    log_info(
        "M365 best practice remediation attempted",
        company_id=company_id,
        check_id=check_id,
        success=success,
    )

    if success:
        return {
            "success": True,
            "message": (
                outcome_message
                or "Remediation command executed successfully. "
                "Re-evaluate the check to confirm the change took effect."
            ),
        }
    return {
        "success": False,
        "message": (
            f"Remediation command failed: {outcome_message}"
            if outcome_message
            else f"Remediation command failed. {generic_failure_reason}"
        ),
    }


# Status constants re-exported for convenience.
__all__ = [
    "STATUS_PASS",
    "STATUS_FAIL",
    "STATUS_UNKNOWN",
    "STATUS_NOT_APPLICABLE",
    "CAP_ENTRA_ID_P1",
    "CAP_ENTRA_ID_P2",
    "CAP_INTUNE",
    "list_best_practices",
    "list_settings_with_catalog",
    "get_enabled_check_ids",
    "get_auto_remediate_check_ids",
    "get_create_ticket_on_fail_check_ids",
    "reset_enabled_results_to_unknown",
    "set_enabled_checks",
    "save_company_exclusions",
    "run_best_practices",
    "run_single_check",
    "get_last_results",
    "set_result_notes",
    "get_secure_score_summary",
    "get_remediation",
    "remediate_check",
    "detect_tenant_capabilities",
]
