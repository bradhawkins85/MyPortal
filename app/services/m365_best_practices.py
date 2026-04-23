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
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Union

import httpx

from app.core.logging import log_error, log_info
from app.repositories import m365_best_practices as bp_repo
from app.services.cis_benchmark import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    STATUS_NOT_APPLICABLE,
    _check_admin_mfa,
    _check_audit_log_enabled,
    _check_global_admin_count,
    _check_guest_access_restricted,
    _check_legacy_auth_blocked,
    _check_mfa_conditional_access,
    _check_monitor_app_credential_expiry,
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
    run_intune_ios_benchmarks,
    run_intune_macos_benchmarks,
    run_intune_windows_benchmarks,
)
from app.services.m365 import (
    M365Error,
    _acquire_exo_access_token,
    _exo_invoke_command,
    _graph_get,
    _graph_get_all,
    _graph_patch,
    _graph_post,
    acquire_access_token,
    acquire_delegated_token,
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
CAP_SHAREPOINT_ONLINE = "sharepoint_online"
CAP_TEAMS = "teams"
CAP_TEAMS_AUDIO_CONF = "teams_audio_conferencing"
CAP_DEFENDER_O365_P1 = "defender_o365_p1"
CAP_DEFENDER_O365_P2 = "defender_o365_p2"
CAP_PURVIEW_DLP = "purview_dlp"
CAP_INTUNE_LAPS = "intune_laps"

# Friendly names used in the "not applicable" details message
_CAPABILITY_FRIENDLY_NAMES: dict[str, str] = {
    CAP_ENTRA_ID_P1: "Microsoft Entra ID P1",
    CAP_ENTRA_ID_P2: "Microsoft Entra ID P2",
    CAP_INTUNE: "Microsoft Intune",
    CAP_EXCHANGE_ONLINE: "Exchange Online",
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
    # EXCHANGE_S_ENTERPRISE (Exchange Online Plan 2)
    "efb87545-963c-4e0d-99df-69c6916d9eb0": {CAP_EXCHANGE_ONLINE},
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
_INTERNAL_KEYS = frozenset({"source", "source_type", "remediation_cmdlet", "remediation_params", "remediation_url", "remediation_payload"})


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
    check_name = "Display concealed user, group, and site names in all reports is enabled"
    try:
        data = await _graph_get(token, _REPORT_SETTINGS_URL)
        display_concealed = data.get("displayConcealedNames")
        if display_concealed is True:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_PASS,
                "details": "Report settings are configured to display real user, group, and site names.",
            }
        if display_concealed is False:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_FAIL,
                "details": (
                    "Report settings are configured to conceal user, group, and site names. "
                    "Enable display of real names to improve report usability and auditability."
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
_AUTHENTICATION_REQUIREMENTS_URL_TMPL = (
    "https://graph.microsoft.com/beta/users/{user_id}/authentication/requirements"
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
_FORMS_SETTINGS_URL = "https://graph.microsoft.com/beta/admin/forms/settings"
_DIRECTORY_SETTINGS_URL = "https://graph.microsoft.com/beta/groupSettings"
_SECURITY_DEFAULTS_URL = (
    "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy"
)
_SPO_SETTINGS_URL = "https://graph.microsoft.com/v1.0/admin/sharepoint/settings"

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


def _result(
    check_id: str, check_name: str, status: str, details: str
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "check_name": check_name,
        "status": status,
        "details": details,
    }


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
# DNS checks (SPF / DMARC via DNS-over-HTTPS)
# ---------------------------------------------------------------------------


async def _check_spf_records_published(token: str) -> dict[str, Any]:
    check_id = "bp_spf_records_published"
    check_name = "SPF records are published for all Exchange Online domains"
    domains_data = await _safe_graph_get_all(token, _DOMAINS_URL)
    if domains_data is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to enumerate accepted domains from Microsoft Graph.")
    exchange_domains = [
        d.get("id") or d.get("name") or ""
        for d in domains_data
        if isinstance(d, dict)
        and d.get("isVerified") is True
        and not str(d.get("id") or "").endswith(".onmicrosoft.com")
    ]
    if not exchange_domains:
        return _result(check_id, check_name, STATUS_PASS,
                       "No custom verified domains found; SPF records are not required.")
    missing: list[str] = []
    errored: list[str] = []
    for domain in exchange_domains:
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
                       f"SPF records found for all {len(exchange_domains)} verified domain(s).")
    suffix = f" (DNS errors for {len(errored)} domain(s))" if errored else ""
    return _result(check_id, check_name, STATUS_FAIL,
                   f"SPF TXT record missing for {len(missing)} domain(s): "
                   + ", ".join(missing[:5]) + suffix
                   + ". Publish: v=spf1 include:spf.protection.outlook.com -all")


async def _check_dmarc_records_published(token: str) -> dict[str, Any]:
    check_id = "bp_dmarc_records_published"
    check_name = "DMARC records for all Exchange Online domains are published"
    domains_data = await _safe_graph_get_all(token, _DOMAINS_URL)
    if domains_data is None:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "Unable to enumerate accepted domains from Microsoft Graph.")
    exchange_domains = [
        d.get("id") or d.get("name") or ""
        for d in domains_data
        if isinstance(d, dict)
        and d.get("isVerified") is True
        and not str(d.get("id") or "").endswith(".onmicrosoft.com")
    ]
    if not exchange_domains:
        return _result(check_id, check_name, STATUS_PASS,
                       "No custom verified domains found; DMARC records are not required.")
    missing: list[str] = []
    errored: list[str] = []
    for domain in exchange_domains:
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
        return _result(check_id, check_name, STATUS_PASS,
                       f"DMARC records found for all {len(exchange_domains)} verified domain(s).")
    suffix = f" (DNS errors for {len(errored)} domain(s))" if errored else ""
    return _result(check_id, check_name, STATUS_FAIL,
                   f"DMARC TXT record missing for {len(missing)} domain(s): "
                   + ", ".join(missing[:5]) + suffix
                   + ". Publish _dmarc.<domain> TXT: v=DMARC1; p=quarantine; rua=mailto:dmarc@<domain>")


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
    enabled_users: list[str] = []
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
            enabled_users.append(
                user.get("userPrincipalName") or user.get("displayName") or user_id
            )
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
    sample = ", ".join(enabled_users[:5])
    suffix = "" if len(enabled_users) <= 5 else f" (and {len(enabled_users) - 5} more)"
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"Per-user MFA is still enabled on {len(enabled_users)} accounts: {sample}{suffix}. "
        "Migrate these users to Conditional Access-driven MFA and disable per-user MFA.",
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
    synced: list[str] = []
    for uid in admin_ids:
        data = await _safe_graph_get(
            token,
            f"https://graph.microsoft.com/v1.0/users/{uid}"
            "?$select=userPrincipalName,onPremisesSyncEnabled",
        )
        if data and data.get("onPremisesSyncEnabled"):
            synced.append(data.get("userPrincipalName") or uid)
    if not synced:
        return _result(
            check_id, check_name, STATUS_PASS,
            f"All {len(admin_ids)} admin accounts are cloud-only.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(synced)} admin account(s) are synced from on-premises AD: " + ", ".join(synced[:5]),
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
    overlicensed: list[str] = []
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
            overlicensed.append(data.get("userPrincipalName") or uid)
    if not overlicensed:
        return _result(
            check_id, check_name, STATUS_PASS,
            "Admin accounts hold a single license SKU.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"Heuristic: {len(overlicensed)} admin account(s) hold multiple license SKUs and "
        "may be candidates for license reduction (manual verification recommended – some "
        "accounts may legitimately require multiple SKUs): " + ", ".join(overlicensed[:5]),
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
    not_capable: list[str] = []
    for row in rows:
        if str(row.get("userType") or "").lower() != "member":
            continue
        if not row.get("isMfaCapable"):
            not_capable.append(row.get("userPrincipalName") or row.get("id") or "?")
    if not not_capable:
        return _result(
            check_id, check_name, STATUS_PASS,
            "All member users are MFA capable.",
        )
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(not_capable)} member user(s) are not MFA capable: " + ", ".join(not_capable[:5]),
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
    return _result(
        check_id, check_name, STATUS_FAIL,
        f"{len(public)} public Microsoft 365 group(s) exist – review and convert unapproved ones to Private: {names}{suffix}.",
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
    fs = data.get("featureSettings") or {}
    missing = [
        k for k in _MFA_FATIGUE_PROTECTION_KEYS
        if str(((fs.get(k) or {}).get("state")) or "").lower() != "enabled"
    ]
    if not missing:
        return _result(check_id, check_name, STATUS_PASS, "All MFA-fatigue protections are enabled.")
    return _result(check_id, check_name, STATUS_FAIL,
                   "Disabled MFA-fatigue protections: " + ", ".join(missing))


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


async def _check_internal_phishing_forms(token: str) -> dict[str, Any]:
    check_id = "bp_internal_phishing_forms"
    check_name = "Internal phishing protection for Microsoft Forms is enabled"
    data = await _safe_graph_get(token, _FORMS_SETTINGS_URL)
    if data is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to read Microsoft Forms settings.")
    if data.get("internalPhishingProtectionEnabled"):
        return _result(check_id, check_name, STATUS_PASS, "Internal phishing protection for Forms is enabled.")
    return _result(check_id, check_name, STATUS_FAIL, "Internal phishing protection for Forms is disabled.")


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


async def _check_two_emergency_access_accounts(token: str) -> dict[str, Any]:
    check_id = "bp_two_emergency_access_accounts"
    check_name = "Two emergency access (break-glass) accounts are defined"
    roles = await _safe_graph_get_all(token, _DIRECTORY_ROLES_URL)
    if roles is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate directory roles.")
    ga_role_id: str | None = None
    for role in roles:
        if str(role.get("roleTemplateId") or "").lower() == _ROLE_TEMPLATE_GLOBAL_ADMIN.lower():
            ga_role_id = role.get("id")
            break
    if not ga_role_id:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Global Administrator role is not currently activated in this tenant.")
    members = await _safe_graph_get_all(
        token, f"https://graph.microsoft.com/v1.0/directoryRoles/{ga_role_id}/members"
    )
    if members is None:
        return _result(check_id, check_name, STATUS_UNKNOWN, "Unable to enumerate Global Administrator members.")
    cloud_only = 0
    for m in members:
        data = await _safe_graph_get(
            token, f"https://graph.microsoft.com/v1.0/users/{m.get('id')}"
            "?$select=onPremisesSyncEnabled,accountEnabled"
        )
        if data and not data.get("onPremisesSyncEnabled") and data.get("accountEnabled"):
            cloud_only += 1
    if cloud_only >= 2:
        return _result(check_id, check_name, STATUS_PASS,
                       f"At least two cloud-only Global Administrator accounts are defined ({cloud_only} found). "
                       "Verify that two of these are dedicated break-glass accounts excluded from MFA enforcement per the tenant runbook.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"Only {cloud_only} cloud-only Global Administrator account(s) found. "
                   "Create at least two dedicated break-glass accounts.")


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
    exo_token: str, tenant_id: str
) -> dict[str, Any]:
    check_id = "bp_dkim_enabled_all_domains"
    check_name = "DKIM is enabled for all Exchange Online domains"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-DkimSigningConfig")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-DkimSigningConfig: {exc}")
    rows = data.get("value") or []
    disabled = [
        r.get("Domain") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict) and r.get("Enabled") is not True
    ]
    if not disabled:
        return _result(check_id, check_name, STATUS_PASS,
                       f"DKIM is enabled for all {len(rows)} configured domains.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"DKIM is disabled on {len(disabled)} domain(s): " + ", ".join(disabled[:5]))


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
    candidates = [
        u for u in users
        if (u.get("userType") or "").lower() == "member"
        and not (u.get("assignedLicenses") or [])
        and u.get("accountEnabled") is True
    ]
    if not candidates:
        return _result(check_id, check_name, STATUS_PASS,
                       "No unlicensed member accounts are sign-in enabled (likely no shared mailbox is sign-in enabled).")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(candidates)} unlicensed member account(s) appear to be sign-in enabled (likely shared mailboxes). "
                   "Disable each via Update-MgUser -UserId <id> -AccountEnabled:$false. "
                   "First sample: " + ", ".join((u.get("userPrincipalName") or u.get("id") or "?") for u in candidates[:5]))


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
    bad: list[str] = []
    required_owner = {"MailboxLogin", "HardDelete", "SoftDelete", "Update"}
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("AuditEnabled") is not True:
            bad.append(r.get("UserPrincipalName") or r.get("Identity") or "?")
            continue
        owner = set(r.get("AuditOwner") or [])
        if not required_owner.issubset(owner):
            bad.append(r.get("UserPrincipalName") or r.get("Identity") or "?")
    if not bad:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Audit actions properly configured on {len(rows)} sampled mailboxes.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(bad)} mailbox(es) lack the recommended audit actions: " + ", ".join(bad[:5]))


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
        if isinstance(r, dict) and r.get("EnableTargetedDomainsProtection") is True
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
        if isinstance(r, dict) and r.get("EnableTargetedUserProtection") is True
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
        if isinstance(r, dict) and r.get("EnableSimilarDomainsSafetyTips") is True
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
        if isinstance(r, dict) and r.get("EnableSimilarUsersSafetyTips") is True
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
        if isinstance(r, dict) and r.get("EnableUnusualCharactersSafetyTips") is True
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
        r.get("UserPrincipalName") or r.get("Identity") or "?"
        for r in rows
        if isinstance(r, dict) and r.get("AuditEnabled") is not True
    ]
    if not not_audited:
        return _result(check_id, check_name, STATUS_PASS,
                       f"Mailbox auditing (AuditEnabled) is enabled on all {len(rows)} user mailbox(es).")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(not_audited)} user mailbox(es) do not have AuditEnabled set to True: "
                   + ", ".join(not_audited[:5])
                   + ("…" if len(not_audited) > 5 else ""))


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

    Calls ``Get-HostedContentFilterPolicy`` and inspects every policy for the
    ``EnableEndUserSpamNotifications`` and ``EndUserSpamNotificationFrequency``
    properties.  Exchange Online supports notification frequencies of 1, 2, or
    3 days; the CIS recommendation (and the intent of a ≤ 4-hour notification
    window) is to set the shortest available interval of 1 day so users are
    alerted to quarantined mail as promptly as possible.
    """
    check_id = "bp_quarantine_notification_enabled"
    check_name = "End-user spam quarantine notifications are enabled with a daily frequency"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-HostedContentFilterPolicy")
    except M365Error as exc:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       f"Unable to query Get-HostedContentFilterPolicy: {exc}")
    rows = data.get("value") or []
    if not rows:
        return _result(check_id, check_name, STATUS_UNKNOWN,
                       "No hosted content filter policies returned.")
    failing: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        enabled = row.get("EnableEndUserSpamNotifications")
        frequency = row.get("EndUserSpamNotificationFrequency")
        name = row.get("Name") or row.get("Identity") or "Default"
        if enabled is not True:
            failing.append(f"{name} (notifications disabled)")
        elif frequency is not None:
            try:
                if int(frequency) > 1:
                    failing.append(f"{name} (frequency={frequency} days; should be 1)")
            except (ValueError, TypeError):
                failing.append(f"{name} (frequency={frequency!r} is not a recognised value)")
    if not failing:
        return _result(check_id, check_name, STATUS_PASS,
                       f"All {len(rows)} hosted content filter "
                       f"{'policy' if len(rows) == 1 else 'policies'} have end-user "
                       "quarantine notifications enabled with a daily frequency.")
    return _result(check_id, check_name, STATUS_FAIL,
                   f"{len(failing)} {'policy does' if len(failing) == 1 else 'policies do'} "
                   "not meet the quarantine notification requirement: "
                   + "; ".join(failing[:5]))


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
            "Microsoft recommends between two and four Global Administrators "
            "to balance availability and minimise blast radius."
        ),
        "remediation": (
            "Adjust Global Administrator role assignments via Azure AD → "
            "Roles and administrators → Global Administrator."
        ),
        "source": _check_global_admin_count,
        "default_enabled": True,
        "has_remediation": False,
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
        "default_enabled": True,
        "has_remediation": False,
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
        "source": _check_monitor_app_credential_expiry,
        "default_enabled": True,
        "has_remediation": False,
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
        "name": "Display concealed user, group, and site names in all reports is enabled",
        "description": (
            "Microsoft 365 usage reports should display real user, group, and site "
            "names so that administrators can accurately audit activity and identify "
            "issues.  When concealed names are enabled, obfuscated identifiers are "
            "shown instead, which reduces the usefulness of usage reports."
        ),
        "remediation": (
            "Run the PowerShell command: "
            "Update-MgAdminReportSetting -DisplayConcealedNames $true\n"
            "Or via the Microsoft 365 admin center: Settings → Org settings → "
            "Services → Reports → enable 'Display concealed user, group, and site names'."
        ),
        "source": _check_concealed_names,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_url": _REPORT_SETTINGS_URL,
        "remediation_payload": {"displayConcealedNames": True},
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "remediation_url": _FORMS_SETTINGS_URL,
        "remediation_payload": {"internalPhishingProtectionEnabled": True},
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
        "id": "bp_two_emergency_access_accounts",
        "name": "Two emergency access (break-glass) accounts are defined",
        "description": (
            "Maintain at least two cloud-only Global Administrator accounts "
            "with strong, well-protected credentials so admins can recover "
            "access if MFA, identity-provider, or federation fails."
        ),
        "remediation": (
            "Create two cloud-only GA accounts (e.g. emergency1@<tenant>.onmicrosoft.com, "
            "emergency2@…), exclude them from all CA policies (storing credentials "
            "in physical safes), and document the recovery runbook."
        ),
        "source": _check_two_emergency_access_accounts,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
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
        "has_remediation": False,
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
        "id": "bp_customer_lockbox",
        "name": "Ensure the customer lockbox feature is enabled",
        "description": (
            "Customer Lockbox ensures that Microsoft cannot access customer "
            "content to perform a service operation without explicit customer "
            "approval. Enabling it provides an additional layer of control and "
            "transparency, allowing organisations to review, approve, or reject "
            "Microsoft engineer access requests to their data."
        ),
        "remediation": "Set-OrganizationConfig -CustomerLockBoxEnabled $true",
        "source": _check_customer_lockbox,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {"CustomerLockBoxEnabled": True},
        "is_cis_benchmark": True,
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
        "name": "DKIM is enabled for all Exchange Online domains",
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
        "has_remediation": False,
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
            "log in to them directly even if they obtain credentials."
        ),
        "remediation": (
            "For each shared mailbox: "
            "Update-MgUser -UserId <upn> -AccountEnabled:$false"
        ),
        "source": _check_shared_mailbox_signin_blocked,
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
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
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-EnableSimilarDomainsSafetyTips $true"
        ),
        "source": _check_antiphish_domain_impersonation_safety_tip,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
            "Set-AntiPhishPolicy -Identity 'Office365 AntiPhish Default' "
            "-EnableSimilarUsersSafetyTips $true"
        ),
        "source": _check_antiphish_user_impersonation_safety_tip,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
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
            "For each hosted content filter policy:\n"
            "Set-HostedContentFilterPolicy -Identity <name> "
            "-EnableEndUserSpamNotifications $true "
            "-EndUserSpamNotificationFrequency 1"
        ),
        "source": _check_quarantine_notification_enabled,
        "source_type": "exo",
        "default_enabled": True,
        "has_remediation": True,
        "remediation_cmdlet": "Set-HostedContentFilterPolicy",
        "remediation_params": {
            "EnableEndUserSpamNotifications": True,
            "EndUserSpamNotificationFrequency": 1,
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
        "source": _manual_review_factory(
            "bp_sharepoint_external_sharing_restricted",
            "SharePoint external sharing is restricted",
            "Manual verification required. Run: Get-SPOSite -Limit All | Select Url,SharingCapability",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "source": _manual_review_factory(
            "bp_anon_dialin_cannot_start_meeting",
            "Anonymous users and dial-in callers can't start a meeting",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | "
            "Select AllowAnonymousUsersToStartMeeting,AllowPSTNUsersToBypassLobby",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
    },
    {
        "id": "bp_only_org_can_bypass_lobby",
        "name": "Only people in my org can bypass the lobby",
        "description": (
            "AutoAdmittedUsers should be restricted to EveryoneInCompany so "
            "external participants always wait in the lobby."
        ),
        "remediation": "Set-CsTeamsMeetingPolicy -Identity Global -AutoAdmittedUsers EveryoneInCompany",
        "source": _manual_review_factory(
            "bp_only_org_can_bypass_lobby",
            "Only people in my org can bypass the lobby",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | Select AutoAdmittedUsers",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
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
        "source": _manual_review_factory(
            "bp_invited_users_auto_admitted",
            "Only invited users should be automatically admitted to Teams meetings",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | Select AutoAdmittedUsers",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
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
        "source": _manual_review_factory(
            "bp_external_participants_no_control",
            "External participants can't give or request control",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | Select AllowExternalParticipantGiveRequestControl",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
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
        "source": _manual_review_factory(
            "bp_external_users_cannot_initiate",
            "External Teams users cannot initiate conversations",
            "Manual verification required. Run: Get-CsTenantFederationConfiguration | "
            "Select AllowFederatedUsers,AllowedDomains",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
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
        "source": _manual_review_factory(
            "bp_teams_external_files_approved_storage",
            "External file sharing in Teams is enabled for only approved cloud storage services",
            "Manual verification required. Run: Get-CsTeamsClientConfiguration -Identity Global | "
            "Select AllowDropBox,AllowGoogleDrive,AllowBox,AllowShareFile,AllowEgnyte",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
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
        "source": _manual_review_factory(
            "bp_restrict_anon_users_join_meeting",
            "Restrict anonymous users from joining meetings",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | "
            "Select AllowAnonymousUsersToJoinMeeting",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
    },
    {
        "id": "bp_restrict_anon_users_start_meeting",
        "name": "Restrict anonymous users from starting Teams meetings",
        "description": (
            "AllowAnonymousUsersToStartMeeting should be set to $false so that "
            "unauthenticated participants cannot start Teams meetings without an "
            "authenticated organiser being present, reducing the risk of "
            "unsupervised meetings and data exposure."
        ),
        "remediation": (
            "Set-CsTeamsMeetingPolicy -Identity Global "
            "-AllowAnonymousUsersToStartMeeting $false"
        ),
        "source": _manual_review_factory(
            "bp_restrict_anon_users_start_meeting",
            "Restrict anonymous users from starting Teams meetings",
            "Manual verification required. Run: Get-CsTeamsMeetingPolicy -Identity Global | "
            "Select AllowAnonymousUsersToStartMeeting",
        ),
        "source_type": "graph",
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_TEAMS],
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
        "default_enabled": True,
        "has_remediation": False,
        "requires_licenses": [CAP_EXCHANGE_ONLINE],
    },
    {
        "id": "bp_dmarc_records_published",
        "name": "DMARC records for all Exchange Online domains are published",
        "description": (
            "DMARC policies tell receivers what to do with mail that fails "
            "SPF/DKIM and provides aggregate reporting on spoof attempts."
        ),
        "remediation": (
            "At your DNS registrar publish a TXT record at _dmarc.<domain>: "
            "v=DMARC1; p=quarantine; rua=mailto:dmarc@<domain>"
        ),
        "source": _check_dmarc_records_published,
        "source_type": "graph",
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
        "source": _manual_review_factory(
            "bp_onprem_password_protection",
            "Password protection is enabled for on-prem Active Directory",
            "Manual verification required. On a domain controller run: "
            "Get-AzureADPasswordProtectionDCAgent. Mark this check as N/A on "
            "the company's exclusion list if the tenant is cloud-only.",
        ),
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

# Mapping from cis_group name to the batch runner function from cis_benchmark.py
_CIS_GROUP_RUNNERS: dict[str, Callable[..., Any]] = {
    "intune_windows": run_intune_windows_benchmarks,
    "intune_ios": run_intune_ios_benchmarks,
    "intune_macos": run_intune_macos_benchmarks,
}


def _enrich_catalog_entry(bp: dict[str, Any]) -> dict[str, Any]:
    """Return a public-facing copy of a catalog entry with internal keys
    stripped and license requirements rendered as a human-friendly string.
    """
    entry = {k: v for k, v in bp.items() if k not in _INTERNAL_KEYS}
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
        if (
            bp.get("has_remediation")
            and check_id in settings
            and settings[check_id].get("auto_remediate")
        ):
            auto_remediate.add(check_id)
    return auto_remediate


async def list_settings_with_catalog(company_id: int | None = None) -> list[dict[str, Any]]:
    """Return the catalog merged with the current global enabled and auto-remediate flags.

    Each item contains the catalog metadata plus:
    - ``enabled`` boolean (global on/off, defaulting to ``default_enabled``)
    - ``auto_remediate`` boolean (auto-remediation after each evaluation)
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
        entry["enabled"] = row["enabled"] if row else bool(bp.get("default_enabled", True))
        entry["auto_remediate"] = row["auto_remediate"] if row else False
        entry["excluded"] = bp["id"] in excluded_ids
        out.append(entry)
    return out


async def set_enabled_checks(
    enabled_check_ids: set[str],
    auto_remediate_check_ids: set[str] | None = None,
) -> None:
    """Persist the global enabled and auto-remediate flags for every catalog check.

    ``enabled_check_ids`` controls which checks are active globally.
    ``auto_remediate_check_ids`` controls which checks trigger automated
    remediation immediately after evaluation (only honoured for checks that
    declare ``has_remediation: True`` in the catalog).

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
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        is_enabled = check_id in enabled_filtered
        is_auto_remediate = check_id in auto_remediate_filtered
        await bp_repo.upsert_setting(
            check_id=check_id,
            enabled=is_enabled,
            auto_remediate=is_auto_remediate,
        )
        if not is_enabled:
            await bp_repo.delete_result_for_check(check_id)
    log_info(
        "M365 Best Practice settings updated",
        enabled_count=len(enabled_filtered),
        auto_remediate_count=len(auto_remediate_filtered),
        total=len(_BEST_PRACTICES),
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


async def run_best_practices(company_id: int) -> list[dict[str, Any]]:
    """Run all globally-enabled best-practice checks for ``company_id``.

    Returns the list of result dicts (one per check) and persists each result
    in the ``m365_best_practice_results`` table.

    After evaluation, any check that both:
    - returned ``STATUS_FAIL``, and
    - has ``auto_remediate`` enabled globally (and ``has_remediation: True``)

    will have automated remediation triggered immediately.

    Graph-based checks receive the Graph access token; Exchange-Online-based
    checks (``source_type == "exo"``) receive the EXO token and tenant ID
    acquired once lazily.  CIS Intune checks (``cis_group`` set) are run via
    their batch runner once per group and results cached for the run.
    """
    graph_token = await acquire_access_token(company_id)

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

    # Cache for CIS batch group results: group_name → {check_id: result_dict}
    cis_group_cache: dict[str, dict[str, dict[str, Any]]] = {}

    results: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id not in enabled or check_id in excluded:
            continue
        check_name = bp["name"]
        cis_group = bp.get("cis_group")

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
                    raw = await _call_check_with_retry(
                        lambda r=runner: r(exo_token, exo_tenant_id),  # type: ignore[call-arg,misc]
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
            except M365Error as exc:
                log_error(
                    "M365 best practice check failed",
                    company_id=company_id,
                    check_id=check_id,
                    error=str(exc),
                )
                status = STATUS_UNKNOWN
                details = f"Unable to evaluate check: {exc}"

        await bp_repo.upsert_result(
            company_id=company_id,
            check_id=check_id,
            check_name=check_name,
            status=status,
            details=details,
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

        results.append({
            "check_id": check_id,
            "check_name": check_name,
            "status": status,
            "details": details,
            "run_at": run_at,
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
            "has_remediation": bool(bp.get("has_remediation")),
        })

    log_info(
        "M365 best practices run",
        company_id=company_id,
        check_count=len(results),
    )
    return results


async def run_single_check(company_id: int, check_id: str) -> dict[str, Any]:
    """Run a single best-practice check by ``check_id`` for ``company_id``.

    Acquires the necessary access tokens, runs only the named check (including
    the self-heal permission grant used by :func:`run_best_practices`), persists
    the result, and returns a result dict in the same shape as the entries
    returned by :func:`run_best_practices`.

    Raises :class:`ValueError` if ``check_id`` is unknown or not currently
    enabled globally.
    """
    catalog = _catalog_map()
    bp = catalog.get(check_id)
    if not bp:
        raise ValueError(f"Unknown best-practice check '{check_id}'")

    enabled = await get_enabled_check_ids()
    if check_id not in enabled:
        raise ValueError(f"Best-practice check '{check_id}' is not enabled")

    graph_token = await acquire_access_token(company_id)

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

    missing = _missing_capabilities(bp.get("requires_licenses"), tenant_capabilities)
    if missing:
        status = STATUS_NOT_APPLICABLE
        details = (
            "Not applicable – this check requires the following Microsoft 365 "
            f"license(s) which the tenant does not have: "
            f"{_format_missing_licenses(missing)}."
        )
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
                raw = await _call_check_with_retry(
                    lambda r=runner: r(exo_token, exo_tenant_id),  # type: ignore[call-arg,misc]
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
        except M365Error as exc:
            log_error(
                "M365 best practice check failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            status = STATUS_UNKNOWN
            details = f"Unable to evaluate check: {exc}"

    await bp_repo.upsert_result(
        company_id=company_id,
        check_id=check_id,
        check_name=check_name,
        status=status,
        details=details,
        run_at=run_at,
    )

    auto_remediate_ids = await get_auto_remediate_check_ids()
    if status == STATUS_FAIL and check_id in auto_remediate_ids:
        log_info(
            "M365 best practice auto-remediation triggered",
            company_id=company_id,
            check_id=check_id,
        )
        await remediate_check(company_id=company_id, check_id=check_id)

    log_info(
        "M365 single best practice check run",
        company_id=company_id,
        check_id=check_id,
    )
    return {
        "check_id": check_id,
        "check_name": check_name,
        "status": status,
        "details": details,
        "run_at": run_at,
        "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
        "has_remediation": bool(bp.get("has_remediation")),
    }


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
        bp_meta = catalog.get(check_id, {})
        status = row.get("status") or STATUS_UNKNOWN
        out.append({
            "check_id": check_id,
            "check_name": row.get("check_name") or bp_meta.get("name", check_id),
            "description": bp_meta.get("description", ""),
            "status": status,
            "details": row.get("details") or "",
            "run_at": row.get("run_at"),
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
            "has_remediation": bool(bp_meta.get("has_remediation")),
            "remediation_status": row.get("remediation_status"),
            "remediated_at": row.get("remediated_at"),
            "is_cis_benchmark": bool(bp_meta.get("is_cis_benchmark")),
            "cis_group": bp_meta.get("cis_group", ""),
        })
    return out


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
        # Skip mailboxes that already satisfy every required parameter value.
        if all(mailbox.get(k) == v for k, v in mailbox_params.items()):
            continue
        try:
            await _exo_invoke_command(
                exo_token, tenant_id, "Set-Mailbox",
                {"Identity": identity, **mailbox_params},
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


async def remediate_check(company_id: int, check_id: str) -> dict[str, Any]:
    """Attempt automated remediation for a single best-practice check.

    Looks up the remediation command from the catalog, executes it via the
    Exchange Online REST API (for EXO-type checks), records the outcome in the
    database, and returns a result dict with ``success`` (bool) and ``message``
    (str) keys.

    Supports three remediation patterns:

    * ``source_type="exo"`` – executes a single cmdlet via the Exchange Online
      REST API using the ``remediation_cmdlet`` and ``remediation_params``
      catalog fields.
    * ``source_type="exo"`` with ``remediation_type="foreach_mailbox_exo"`` –
      fetches all user mailboxes and calls ``Set-Mailbox`` on each one that does
      not already satisfy the required parameters using the
      ``remediation_mailbox_params`` catalog field.
    * ``source_type="graph"`` – issues a ``PATCH`` request to Microsoft Graph
      using the ``remediation_url`` and ``remediation_payload`` catalog fields.
    """
    bp = _catalog_map().get(check_id)
    if not bp or not bp.get("has_remediation"):
        return {
            "success": False,
            "message": "Automated remediation is not available for this check.",
        }

    source_type = bp.get("source_type", "graph")
    remediated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if source_type == "exo":
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
            )
            return {
                "success": False,
                "message": "Unable to acquire Exchange Online token. Check that the app credentials are correct.",
            }

        if bp.get("remediation_type") == "foreach_mailbox_exo":
            mailbox_params = bp.get("remediation_mailbox_params") or {}
            success = await _remediate_foreach_mailbox(
                exo_token, tenant_id, company_id, check_id, mailbox_params
            )
        else:
            cmdlet = bp.get("remediation_cmdlet", "")
            params = bp.get("remediation_params") or {}
            try:
                await _exo_invoke_command(exo_token, tenant_id, cmdlet, params)
                success = True
            except M365Error as exc:
                log_error(
                    "M365 best practice remediation command failed",
                    company_id=company_id,
                    check_id=check_id,
                    cmdlet=cmdlet,
                    error=str(exc),
                )
                success = False
    elif source_type == "graph":
        remediation_url = bp.get("remediation_url", "")
        remediation_payload = bp.get("remediation_payload") or {}
        try:
            graph_token = await acquire_access_token(company_id)
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
            )
            return {
                "success": False,
                "message": "Unable to acquire Microsoft Graph token. Check that the app credentials are correct.",
            }
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
    else:
        success = False

    remediation_status = "success" if success else "failed"
    await bp_repo.update_remediation_status(
        company_id=company_id,
        check_id=check_id,
        remediation_status=remediation_status,
        remediated_at=remediated_at,
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
                "Remediation command executed successfully. "
                "Re-evaluate the check to confirm the change took effect."
            ),
        }
    return {
        "success": False,
        "message": "Remediation command failed. Check that the app has the required permissions.",
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
    "set_enabled_checks",
    "save_company_exclusions",
    "run_best_practices",
    "run_single_check",
    "get_last_results",
    "get_remediation",
    "remediate_check",
    "detect_tenant_capabilities",
]
