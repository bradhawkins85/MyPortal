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
    _graph_patch,
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

# Friendly names used in the "not applicable" details message
_CAPABILITY_FRIENDLY_NAMES: dict[str, str] = {
    CAP_ENTRA_ID_P1: "Microsoft Entra ID P1",
    CAP_ENTRA_ID_P2: "Microsoft Entra ID P2",
    CAP_INTUNE: "Microsoft Intune",
}

# Service plan GUIDs (lower-case) that grant each capability.  Entra ID P2
# always includes Entra ID P1 features.
_SERVICE_PLAN_TO_CAPABILITIES: dict[str, set[str]] = {
    # AAD_PREMIUM (Entra ID P1)
    "41781fb2-bc02-4b7c-bd55-b576c07bb09d": {CAP_ENTRA_ID_P1},
    # AAD_PREMIUM_P2 (Entra ID P2 – includes P1)
    "eec0eb4f-6444-4f95-aba0-50c24d67f998": {CAP_ENTRA_ID_P1, CAP_ENTRA_ID_P2},
    # INTUNE_A (Microsoft Intune)
    "c1ec4a95-1f05-45b3-a911-aa3fa01094f5": {CAP_INTUNE},
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


async def remediate_check(company_id: int, check_id: str) -> dict[str, Any]:
    """Attempt automated remediation for a single best-practice check.

    Looks up the remediation command from the catalog, executes it via the
    Exchange Online REST API (for EXO-type checks), records the outcome in the
    database, and returns a result dict with ``success`` (bool) and ``message``
    (str) keys.

    Supports two remediation source types:

    * ``"exo"`` – executes a cmdlet via the Exchange Online REST API using the
      ``remediation_cmdlet`` and ``remediation_params`` catalog fields.
    * ``"graph"`` – issues a ``PATCH`` request to Microsoft Graph using the
      ``remediation_url`` and ``remediation_payload`` catalog fields.
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
