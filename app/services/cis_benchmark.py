"""CIS Benchmark service for Microsoft 365, Intune Windows, iOS, macOS.

Implements a subset of the CIS Microsoft 365 Foundations Benchmark and the
CIS Microsoft Intune for Windows/iOS/macOS benchmarks using the Microsoft
Graph API.  Each benchmark check returns a pass/fail/unknown status along
with a human-readable details message and remediation guidance.

The additional Graph application permissions required beyond the base
provisioning set are declared in ``_CIS_BENCHMARK_APP_ROLES`` in
``app/services/m365.py`` and must be granted before these checks will
return meaningful results.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import cis_benchmarks as benchmark_repo
from app.services.m365 import M365Error, _graph_get, acquire_access_token


# ---------------------------------------------------------------------------
# Check status constants
# ---------------------------------------------------------------------------

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_EXCLUDED = "excluded"

# ---------------------------------------------------------------------------
# Benchmark category constants
# ---------------------------------------------------------------------------

CATEGORY_M365 = "m365"
CATEGORY_INTUNE_WINDOWS = "intune_windows"
CATEGORY_INTUNE_IOS = "intune_ios"
CATEGORY_INTUNE_MACOS = "intune_macos"

# All supported benchmark categories in display order
BENCHMARK_CATEGORIES: list[dict[str, str]] = [
    {
        "id": CATEGORY_M365,
        "name": "Microsoft 365 Foundations",
        "description": "CIS Microsoft 365 Foundations Benchmark – security baseline checks for M365 tenants.",
    },
    {
        "id": CATEGORY_INTUNE_WINDOWS,
        "name": "Microsoft Intune for Windows",
        "description": "CIS Microsoft Intune for Windows Benchmark – device compliance and configuration checks.",
    },
    {
        "id": CATEGORY_INTUNE_IOS,
        "name": "Microsoft Intune for iOS / iPadOS",
        "description": "CIS Microsoft Intune for iOS/iPadOS Benchmark – mobile device compliance checks.",
    },
    {
        "id": CATEGORY_INTUNE_MACOS,
        "name": "Microsoft Intune for macOS",
        "description": "CIS Microsoft Intune for macOS Benchmark – macOS device compliance checks.",
    },
]


# ---------------------------------------------------------------------------
# Remediation guidance per check_id
# ---------------------------------------------------------------------------

_REMEDIATION: dict[str, str] = {
    # M365 checks
    "m365_security_defaults": (
        "Enable Security Defaults in Azure AD: "
        "Azure portal → Azure Active Directory → Properties → Manage security defaults → Enable."
    ),
    "m365_mfa_conditional_access": (
        "Create a Conditional Access policy requiring MFA for all users: "
        "Azure portal → Azure Active Directory → Security → Conditional Access → New policy → "
        "Assign to All users, require Multi-factor authentication under Grant."
    ),
    "m365_legacy_auth_blocked": (
        "Block legacy authentication via Conditional Access: "
        "Create a policy targeting 'Exchange ActiveSync clients' and 'Other clients', block access."
    ),
    "m365_admin_mfa": (
        "Ensure all admin role holders are registered for MFA and have it enforced. "
        "Review via Azure AD → Users → Per-user MFA and ensure Conditional Access covers admins."
    ),
    "m365_global_admin_count": (
        "Reduce the number of Global Administrators to between 2 and 4. "
        "Review role assignments: Azure AD → Roles and administrators → Global Administrator."
    ),
    "m365_audit_log_enabled": (
        "Enable the unified audit log in the Microsoft 365 compliance portal: "
        "Compliance portal → Audit → Start recording user and admin activity."
    ),
    "m365_self_service_password_reset": (
        "Enable Self-Service Password Reset (SSPR): "
        "Azure AD → Password reset → Properties → Enable for All users."
    ),
    "m365_password_expiry_disabled": (
        "Set passwords to never expire for cloud-only accounts (MFA compensates): "
        "Microsoft 365 Admin Center → Settings → Org Settings → Security & privacy → Password expiration policy."
    ),
    "m365_guest_access_restricted": (
        "Restrict guest user access: "
        "Azure AD → External Identities → External collaboration settings → "
        "Guest user access = 'most restrictive' and limit invite permissions."
    ),
    "m365_external_sharing_restricted": (
        "Restrict SharePoint/OneDrive external sharing to existing guests only or disable entirely: "
        "SharePoint Admin Center → Policies → Sharing → Limit external sharing."
    ),
    # Intune Windows checks
    "intune_windows_encryption": (
        "Create a Windows device compliance policy requiring BitLocker encryption: "
        "Intune → Devices → Compliance policies → Create policy → Windows 10/11 → "
        "System Security → Require BitLocker = Require."
    ),
    "intune_windows_firewall": (
        "Require Windows Firewall in the device compliance policy: "
        "Intune → Devices → Compliance policies → Windows policy → "
        "System Security → Firewall = Require."
    ),
    "intune_windows_antivirus": (
        "Require antivirus in the Windows device compliance policy: "
        "Intune → Devices → Compliance policies → Windows policy → "
        "System Security → Antivirus = Require."
    ),
    "intune_windows_secure_boot": (
        "Require Secure Boot in the Windows device compliance policy: "
        "Intune → Devices → Compliance policies → Windows policy → "
        "System Security → Secure Boot enabled = Require."
    ),
    "intune_windows_min_os": (
        "Set a minimum supported OS version in the Windows compliance policy: "
        "Intune → Devices → Compliance policies → Windows policy → "
        "Device Properties → Minimum OS version."
    ),
    "intune_windows_compliance_policy_exists": (
        "Create at least one Windows device compliance policy: "
        "Intune → Devices → Compliance policies → Create policy → Windows 10 and later."
    ),
    # Intune iOS checks
    "intune_ios_compliance_policy_exists": (
        "Create at least one iOS/iPadOS device compliance policy: "
        "Intune → Devices → Compliance policies → Create policy → iOS/iPadOS."
    ),
    "intune_ios_passcode_required": (
        "Require a passcode/PIN in the iOS compliance policy: "
        "Intune → Compliance policies → iOS policy → System Security → Require a password."
    ),
    "intune_ios_jailbreak_blocked": (
        "Block jailbroken devices in the iOS compliance policy: "
        "Intune → Compliance policies → iOS policy → Device Health → "
        "Jailbroken devices = Block."
    ),
    "intune_ios_min_os": (
        "Set a minimum supported iOS version in the compliance policy: "
        "Intune → Compliance policies → iOS policy → Device Properties → Minimum OS version."
    ),
    # Intune macOS checks
    "intune_macos_compliance_policy_exists": (
        "Create at least one macOS device compliance policy: "
        "Intune → Devices → Compliance policies → Create policy → macOS."
    ),
    "intune_macos_filevault": (
        "Require FileVault disk encryption in the macOS compliance policy: "
        "Intune → Compliance policies → macOS policy → System Security → "
        "Require encryption of data storage on device."
    ),
    "intune_macos_firewall": (
        "Require the macOS Firewall in the compliance policy: "
        "Intune → Compliance policies → macOS policy → System Security → Firewall."
    ),
    "intune_macos_min_os": (
        "Set a minimum supported macOS version in the compliance policy: "
        "Intune → Compliance policies → macOS policy → Device Properties → Minimum OS version."
    ),
    "intune_macos_gatekeeper": (
        "Require Gatekeeper in the macOS compliance policy: "
        "Intune → Compliance policies → macOS policy → System Security → Gatekeeper."
    ),
}


def get_remediation(check_id: str) -> str:
    return _REMEDIATION.get(check_id, "Consult the CIS Benchmark documentation for remediation guidance.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check(
    check_id: str,
    check_name: str,
    status: str,
    details: str,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "check_name": check_name,
        "status": status,
        "details": details,
        "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
    }


def _pass(check_id: str, check_name: str, details: str) -> dict[str, Any]:
    return _check(check_id, check_name, STATUS_PASS, details)


def _fail(check_id: str, check_name: str, details: str) -> dict[str, Any]:
    return _check(check_id, check_name, STATUS_FAIL, details)


def _unknown(check_id: str, check_name: str, details: str) -> dict[str, Any]:
    return _check(check_id, check_name, STATUS_UNKNOWN, details)


# ---------------------------------------------------------------------------
# M365 benchmark checks
# ---------------------------------------------------------------------------

async def _check_security_defaults(token: str) -> dict[str, Any]:
    check_id = "m365_security_defaults"
    check_name = "Security Defaults enabled"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy",
        )
        enabled = data.get("isEnabled", False)
        if enabled:
            return _pass(check_id, check_name, "Security Defaults are enabled.")
        return _fail(check_id, check_name, "Security Defaults are disabled.")
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve policy: {exc}")


async def _check_legacy_auth_blocked(token: str) -> dict[str, Any]:
    check_id = "m365_legacy_auth_blocked"
    check_name = "Legacy authentication is blocked"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/policies/conditionalAccessPolicies"
            "?$select=id,displayName,state,conditions,grantControls",
        )
        policies = data.get("value", [])
        legacy_auth_clients = {
            "exchangeActiveSync",
            "other",
        }
        for policy in policies:
            if policy.get("state") != "enabled":
                continue
            conditions = policy.get("conditions") or {}
            client_apps = set((conditions.get("clientAppTypes") or []))
            grant = policy.get("grantControls") or {}
            built_in = grant.get("builtInControls") or []
            if "block" not in built_in:
                continue
            if legacy_auth_clients.issubset(client_apps):
                return _pass(check_id, check_name, f"Policy '{policy.get('displayName')}' blocks legacy authentication.")
        return _fail(check_id, check_name, "No enabled Conditional Access policy found that blocks legacy authentication.")
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve Conditional Access policies: {exc}")


async def _check_mfa_conditional_access(token: str) -> dict[str, Any]:
    check_id = "m365_mfa_conditional_access"
    check_name = "MFA required for all users via Conditional Access"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/policies/conditionalAccessPolicies"
            "?$select=id,displayName,state,conditions,grantControls",
        )
        policies = data.get("value", [])
        for policy in policies:
            if policy.get("state") != "enabled":
                continue
            conditions = policy.get("conditions") or {}
            users = conditions.get("users") or {}
            include_users = users.get("includeUsers") or []
            include_groups = users.get("includeGroups") or []
            if "All" not in include_users and not include_groups:
                continue
            grant = policy.get("grantControls") or {}
            built_in = grant.get("builtInControls") or []
            if "mfa" in built_in:
                return _pass(check_id, check_name, f"Policy '{policy.get('displayName')}' requires MFA for all users.")
        return _fail(check_id, check_name, "No enabled Conditional Access policy found requiring MFA for all users.")
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve Conditional Access policies: {exc}")


async def _check_global_admin_count(token: str) -> dict[str, Any]:
    check_id = "m365_global_admin_count"
    check_name = "Global Administrator count is between 2 and 4"
    try:
        # Get the Global Administrator role
        roles_data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/directoryRoles"
            "?$filter=displayName eq 'Global Administrator'&$select=id,displayName",
        )
        roles = roles_data.get("value", [])
        if not roles:
            return _unknown(check_id, check_name, "Global Administrator directory role not found.")
        role_id = roles[0]["id"]
        members_data = await _graph_get(
            token,
            f"https://graph.microsoft.com/v1.0/directoryRoles/{role_id}/members?$select=id",
        )
        members = members_data.get("value", [])
        count = len(members)
        if 2 <= count <= 4:
            return _pass(check_id, check_name, f"There are {count} Global Administrators (recommended: 2–4).")
        return _fail(
            check_id,
            check_name,
            f"There are {count} Global Administrators. CIS recommends between 2 and 4.",
        )
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve directory role members: {exc}")


async def _check_audit_log_enabled(token: str) -> dict[str, Any]:
    check_id = "m365_audit_log_enabled"
    check_name = "Unified audit log is enabled"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/security/auditLog/queries?$top=1",
        )
        # If the endpoint is accessible, auditing is provisioned.  A 403 or
        # missing permission will have thrown M365Error above.
        _ = data
        return _pass(check_id, check_name, "Unified audit log endpoint is accessible – audit logging is enabled.")
    except M365Error as exc:
        err = str(exc)
        if "403" in err or "Forbidden" in err or "Authorization" in err:
            return _fail(check_id, check_name, "Audit log endpoint returned Forbidden – auditing may be disabled or the app lacks AuditLog.Read.All permission.")
        return _unknown(check_id, check_name, f"Unable to verify audit log status: {exc}")


async def _check_sspr_enabled(token: str) -> dict[str, Any]:
    check_id = "m365_self_service_password_reset"
    check_name = "Self-Service Password Reset (SSPR) is enabled"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/policies/authorizationPolicy?$select=allowedToUseSspr",
        )
        allowed = data.get("allowedToUseSspr", None)
        if allowed is True:
            return _pass(check_id, check_name, "SSPR is enabled for users.")
        if allowed is False:
            return _fail(check_id, check_name, "SSPR is not enabled for users.")
        return _unknown(check_id, check_name, "Unable to determine SSPR status from policy response.")
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve authorization policy: {exc}")


async def _check_password_never_expires(token: str) -> dict[str, Any]:
    check_id = "m365_password_expiry_disabled"
    check_name = "Password expiry disabled for cloud accounts (MFA compensates)"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/domains?$select=id,passwordValidityPeriodInDays,isDefault",
        )
        domains = data.get("value", [])
        non_compliant = [
            d["id"]
            for d in domains
            if d.get("passwordValidityPeriodInDays") not in (None, 0, 2147483647)
        ]
        if not non_compliant:
            return _pass(check_id, check_name, "Password expiry is disabled (set to never) for all verified domains.")
        return _fail(
            check_id,
            check_name,
            f"Password expiry is configured on domain(s): {', '.join(non_compliant)}. "
            "CIS recommends setting passwords to never expire when MFA is enforced.",
        )
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve domain password policy: {exc}")


async def _check_guest_access_restricted(token: str) -> dict[str, Any]:
    check_id = "m365_guest_access_restricted"
    check_name = "Guest user access is restricted"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/policies/authorizationPolicy"
            "?$select=guestUserRoleId,allowInvitesFrom",
        )
        # guestUserRoleId values:
        #   10dae51f-b6af-4016-8d66-8c2a99b929b3 = Guest user (most restrictive)
        #   2af84b1e-32c8-42b7-82bc-daa82404023b = Guest user (limited access)
        #   a0b1b346-4d3e-4e8b-98f8-753987be4970 = Member-like guest (least restrictive)
        restricted_ids = {
            "10dae51f-b6af-4016-8d66-8c2a99b929b3",
            "2af84b1e-32c8-42b7-82bc-daa82404023b",
        }
        role_id = str(data.get("guestUserRoleId") or "").lower()
        allow_invites = str(data.get("allowInvitesFrom") or "")
        if not role_id:
            return _unknown(check_id, check_name, "guestUserRoleId not returned by the API – unable to evaluate guest access restriction.")
        role_ok = role_id in restricted_ids
        invite_ok = allow_invites in ("adminsAndGuestInviters", "adminsGuestInvitersAndAllMembers", "none", "")
        if role_ok and invite_ok:
            return _pass(check_id, check_name, "Guest access role and invite permissions are appropriately restricted.")
        issues = []
        if not role_ok:
            issues.append(f"guestUserRoleId is '{role_id}' (least restrictive; member-like access)")
        if not invite_ok:
            issues.append(f"allowInvitesFrom is '{allow_invites}' (allows broad invite permissions)")
        return _fail(check_id, check_name, "; ".join(issues))
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve authorization policy: {exc}")


async def _check_admin_mfa(token: str) -> dict[str, Any]:
    check_id = "m365_admin_mfa"
    check_name = "MFA required for administrative roles"
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/policies/conditionalAccessPolicies"
            "?$select=id,displayName,state,conditions,grantControls",
        )
        policies = data.get("value", [])
        admin_role_ids = {
            "62e90394-69f5-4237-9190-012177145e10",  # Global Administrator
            "194ae4cb-b126-40b2-bd5b-6091b380977d",  # Security Administrator
            "f28a1f50-f6e7-4571-818b-6a12f2af6b6c",  # SharePoint Administrator
        }
        for policy in policies:
            if policy.get("state") != "enabled":
                continue
            conditions = policy.get("conditions") or {}
            users = conditions.get("users") or {}
            included_roles = set(users.get("includeRoles") or [])
            include_all_users = "All" in (users.get("includeUsers") or [])
            grant = policy.get("grantControls") or {}
            if "mfa" not in (grant.get("builtInControls") or []):
                continue
            if include_all_users or admin_role_ids.issubset(included_roles) or bool(admin_role_ids & included_roles):
                return _pass(
                    check_id,
                    check_name,
                    f"Policy '{policy.get('displayName')}' enforces MFA for administrative roles.",
                )
        return _fail(check_id, check_name, "No enabled Conditional Access policy found that enforces MFA for administrative roles.")
    except M365Error as exc:
        return _unknown(check_id, check_name, f"Unable to retrieve Conditional Access policies: {exc}")


async def run_m365_benchmarks(token: str) -> list[dict[str, Any]]:
    """Run all M365 Foundations benchmark checks and return results."""
    results = []
    checks = [
        _check_security_defaults,
        _check_legacy_auth_blocked,
        _check_mfa_conditional_access,
        _check_admin_mfa,
        _check_global_admin_count,
        _check_audit_log_enabled,
        _check_sspr_enabled,
        _check_password_never_expires,
        _check_guest_access_restricted,
    ]
    for check_fn in checks:
        try:
            result = await check_fn(token)
            results.append(result)
        except Exception as exc:
            log_error("Unexpected error in M365 benchmark check", check=check_fn.__name__, error=str(exc))
    return results


# ---------------------------------------------------------------------------
# Intune Windows benchmark checks
# ---------------------------------------------------------------------------

def _windows_compliance_check(
    check_id: str,
    check_name: str,
    policies: list[dict[str, Any]],
    property_path: list[str],
    expected_value: Any,
    expected_display: str,
    pass_display: str,
) -> dict[str, Any]:
    """Check a specific compliance policy setting across all Windows policies."""
    windows_policies = [
        p for p in policies
        if "windows" in str(p.get("@odata.type", "")).lower()
    ]
    if not windows_policies:
        return _fail(check_id, check_name, "No Windows device compliance policies found.")
    compliant = []
    non_compliant = []
    for policy in windows_policies:
        name = policy.get("displayName", "Unnamed")
        obj = policy
        for key in property_path:
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                obj = None
                break
        if obj == expected_value:
            compliant.append(name)
        else:
            non_compliant.append(f"{name} (value: {obj!r}, expected: {expected_display!r})")
    if non_compliant:
        return _fail(check_id, check_name, f"Non-compliant policies: {'; '.join(non_compliant)}")
    return _pass(check_id, check_name, f"{pass_display}: {', '.join(compliant)}")


async def run_intune_windows_benchmarks(token: str) -> list[dict[str, Any]]:
    """Run all Intune Windows benchmark checks."""
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/deviceManagement/deviceCompliancePolicies",
        )
        all_policies = data.get("value", [])
    except M365Error as exc:
        msg = f"Unable to retrieve device compliance policies: {exc}"
        return [
            _unknown(cid, cname, msg)
            for cid, cname in [
                ("intune_windows_compliance_policy_exists", "Windows compliance policy exists"),
                ("intune_windows_encryption", "BitLocker encryption required"),
                ("intune_windows_firewall", "Windows Firewall required"),
                ("intune_windows_antivirus", "Antivirus required"),
                ("intune_windows_secure_boot", "Secure Boot required"),
                ("intune_windows_min_os", "Minimum OS version configured"),
            ]
        ]

    windows_policies = [
        p for p in all_policies
        if "windows" in str(p.get("@odata.type", "")).lower()
    ]

    results: list[dict[str, Any]] = []

    # Check 1: At least one Windows compliance policy exists
    if windows_policies:
        results.append(_pass(
            "intune_windows_compliance_policy_exists",
            "Windows compliance policy exists",
            f"Found {len(windows_policies)} Windows device compliance policy(s).",
        ))
    else:
        results.append(_fail(
            "intune_windows_compliance_policy_exists",
            "Windows compliance policy exists",
            "No Windows device compliance policies found in Intune.",
        ))
        # Without policies, remaining checks are not applicable
        na_checks = [
            ("intune_windows_encryption", "BitLocker encryption required"),
            ("intune_windows_firewall", "Windows Firewall required"),
            ("intune_windows_antivirus", "Antivirus required"),
            ("intune_windows_secure_boot", "Secure Boot required"),
            ("intune_windows_min_os", "Minimum OS version configured"),
        ]
        for cid, cname in na_checks:
            results.append(_check(cid, cname, STATUS_NOT_APPLICABLE, "No Windows compliance policies exist."))
        return results

    results.append(_windows_compliance_check(
        "intune_windows_encryption",
        "BitLocker encryption required",
        windows_policies,
        ["bitLockerEnabled"],
        True,
        "true",
        "All Windows policies require BitLocker",
    ))
    results.append(_windows_compliance_check(
        "intune_windows_firewall",
        "Windows Firewall required",
        windows_policies,
        ["firewallEnabled"],
        True,
        "true",
        "All Windows policies require Firewall",
    ))
    results.append(_windows_compliance_check(
        "intune_windows_antivirus",
        "Antivirus required",
        windows_policies,
        ["antivirusRequired"],
        True,
        "true",
        "All Windows policies require Antivirus",
    ))
    results.append(_windows_compliance_check(
        "intune_windows_secure_boot",
        "Secure Boot required",
        windows_policies,
        ["secureBootEnabled"],
        True,
        "true",
        "All Windows policies require Secure Boot",
    ))

    # Minimum OS check – just needs a non-empty value
    has_min_os = [
        p.get("displayName", "Unnamed")
        for p in windows_policies
        if p.get("osMinimumVersion")
    ]
    missing_min_os = [
        p.get("displayName", "Unnamed")
        for p in windows_policies
        if not p.get("osMinimumVersion")
    ]
    if missing_min_os:
        results.append(_fail(
            "intune_windows_min_os",
            "Minimum OS version configured",
            f"Policies without minimum OS version: {', '.join(missing_min_os)}.",
        ))
    else:
        results.append(_pass(
            "intune_windows_min_os",
            "Minimum OS version configured",
            f"All Windows policies have a minimum OS version: {', '.join(has_min_os)}.",
        ))

    return results


# ---------------------------------------------------------------------------
# Intune iOS benchmark checks
# ---------------------------------------------------------------------------

async def run_intune_ios_benchmarks(token: str) -> list[dict[str, Any]]:
    """Run all Intune iOS/iPadOS benchmark checks."""
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/deviceManagement/deviceCompliancePolicies",
        )
        all_policies = data.get("value", [])
    except M365Error as exc:
        msg = f"Unable to retrieve device compliance policies: {exc}"
        return [
            _unknown(cid, cname, msg)
            for cid, cname in [
                ("intune_ios_compliance_policy_exists", "iOS compliance policy exists"),
                ("intune_ios_passcode_required", "Passcode required"),
                ("intune_ios_jailbreak_blocked", "Jailbroken devices blocked"),
                ("intune_ios_min_os", "Minimum OS version configured"),
            ]
        ]

    ios_policies = [
        p for p in all_policies
        if "ios" in str(p.get("@odata.type", "")).lower()
    ]

    results: list[dict[str, Any]] = []

    if not ios_policies:
        results.append(_fail(
            "intune_ios_compliance_policy_exists",
            "iOS compliance policy exists",
            "No iOS/iPadOS device compliance policies found in Intune.",
        ))
        for cid, cname in [
            ("intune_ios_passcode_required", "Passcode required"),
            ("intune_ios_jailbreak_blocked", "Jailbroken devices blocked"),
            ("intune_ios_min_os", "Minimum OS version configured"),
        ]:
            results.append(_check(cid, cname, STATUS_NOT_APPLICABLE, "No iOS compliance policies exist."))
        return results

    results.append(_pass(
        "intune_ios_compliance_policy_exists",
        "iOS compliance policy exists",
        f"Found {len(ios_policies)} iOS/iPadOS device compliance policy(s).",
    ))

    # Passcode required
    no_passcode = [p.get("displayName", "Unnamed") for p in ios_policies if not p.get("passcodeRequired")]
    if no_passcode:
        results.append(_fail("intune_ios_passcode_required", "Passcode required", f"Policies without passcode required: {', '.join(no_passcode)}."))
    else:
        results.append(_pass("intune_ios_passcode_required", "Passcode required", "All iOS policies require a passcode."))

    # Jailbreak blocked
    no_jailbreak_block = [
        p.get("displayName", "Unnamed")
        for p in ios_policies
        if not p.get("deviceThreatProtectionRequiredSecurityLevel") and not p.get("jailBroken") == "Block"
    ]
    # Many policies use securityRequireVerifyApps or managedEmailProfileRequired; check jailBroken field
    jailbreak_blocked = [
        p.get("displayName", "Unnamed")
        for p in ios_policies
        if str(p.get("jailBroken") or "").lower() in ("block", "true", "blocked")
    ]
    if jailbreak_blocked:
        results.append(_pass("intune_ios_jailbreak_blocked", "Jailbroken devices blocked",
                             f"Policies blocking jailbroken devices: {', '.join(jailbreak_blocked)}."))
    else:
        results.append(_fail("intune_ios_jailbreak_blocked", "Jailbroken devices blocked",
                             "No iOS policies found that block jailbroken devices."))

    # Minimum OS
    missing_min_os = [p.get("displayName", "Unnamed") for p in ios_policies if not p.get("osMinimumVersion")]
    if missing_min_os:
        results.append(_fail("intune_ios_min_os", "Minimum OS version configured",
                             f"Policies without minimum OS version: {', '.join(missing_min_os)}."))
    else:
        results.append(_pass("intune_ios_min_os", "Minimum OS version configured",
                             "All iOS policies have a minimum OS version configured."))

    return results


# ---------------------------------------------------------------------------
# Intune macOS benchmark checks
# ---------------------------------------------------------------------------

async def run_intune_macos_benchmarks(token: str) -> list[dict[str, Any]]:
    """Run all Intune macOS benchmark checks."""
    try:
        data = await _graph_get(
            token,
            "https://graph.microsoft.com/v1.0/deviceManagement/deviceCompliancePolicies",
        )
        all_policies = data.get("value", [])
    except M365Error as exc:
        msg = f"Unable to retrieve device compliance policies: {exc}"
        return [
            _unknown(cid, cname, msg)
            for cid, cname in [
                ("intune_macos_compliance_policy_exists", "macOS compliance policy exists"),
                ("intune_macos_filevault", "FileVault disk encryption required"),
                ("intune_macos_firewall", "macOS Firewall required"),
                ("intune_macos_min_os", "Minimum OS version configured"),
                ("intune_macos_gatekeeper", "Gatekeeper enabled"),
            ]
        ]

    macos_policies = [
        p for p in all_policies
        if "macos" in str(p.get("@odata.type", "")).lower()
    ]

    results: list[dict[str, Any]] = []

    if not macos_policies:
        results.append(_fail(
            "intune_macos_compliance_policy_exists",
            "macOS compliance policy exists",
            "No macOS device compliance policies found in Intune.",
        ))
        for cid, cname in [
            ("intune_macos_filevault", "FileVault disk encryption required"),
            ("intune_macos_firewall", "macOS Firewall required"),
            ("intune_macos_min_os", "Minimum OS version configured"),
            ("intune_macos_gatekeeper", "Gatekeeper enabled"),
        ]:
            results.append(_check(cid, cname, STATUS_NOT_APPLICABLE, "No macOS compliance policies exist."))
        return results

    results.append(_pass(
        "intune_macos_compliance_policy_exists",
        "macOS compliance policy exists",
        f"Found {len(macos_policies)} macOS device compliance policy(s).",
    ))

    # FileVault
    no_fv = [p.get("displayName", "Unnamed") for p in macos_policies if not p.get("storageRequireEncryption")]
    if no_fv:
        results.append(_fail("intune_macos_filevault", "FileVault disk encryption required",
                             f"Policies without FileVault required: {', '.join(no_fv)}."))
    else:
        results.append(_pass("intune_macos_filevault", "FileVault disk encryption required",
                             "All macOS policies require FileVault encryption."))

    # Firewall
    no_fw = [p.get("displayName", "Unnamed") for p in macos_policies if not p.get("firewallEnabled")]
    if no_fw:
        results.append(_fail("intune_macos_firewall", "macOS Firewall required",
                             f"Policies without Firewall required: {', '.join(no_fw)}."))
    else:
        results.append(_pass("intune_macos_firewall", "macOS Firewall required",
                             "All macOS policies require the Firewall."))

    # Minimum OS
    missing_min_os = [p.get("displayName", "Unnamed") for p in macos_policies if not p.get("osMinimumVersion")]
    if missing_min_os:
        results.append(_fail("intune_macos_min_os", "Minimum OS version configured",
                             f"Policies without minimum OS version: {', '.join(missing_min_os)}."))
    else:
        results.append(_pass("intune_macos_min_os", "Minimum OS version configured",
                             "All macOS policies have a minimum OS version configured."))

    # Gatekeeper
    no_gk = [p.get("displayName", "Unnamed") for p in macos_policies if not p.get("gatekeeperAllowedAppSource")]
    if no_gk:
        results.append(_fail("intune_macos_gatekeeper", "Gatekeeper enabled",
                             f"Policies without Gatekeeper configured: {', '.join(no_gk)}."))
    else:
        results.append(_pass("intune_macos_gatekeeper", "Gatekeeper enabled",
                             "All macOS policies have Gatekeeper configured."))

    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_CATEGORY_RUNNERS = {
    CATEGORY_M365: run_m365_benchmarks,
    CATEGORY_INTUNE_WINDOWS: run_intune_windows_benchmarks,
    CATEGORY_INTUNE_IOS: run_intune_ios_benchmarks,
    CATEGORY_INTUNE_MACOS: run_intune_macos_benchmarks,
}


async def run_benchmarks(company_id: int, categories: list[str] | None = None) -> dict[str, Any]:
    """Run CIS benchmark checks for the given company.

    Acquires a fresh access token and runs each requested benchmark category.
    Results are stored in the database and returned.

    :param company_id: The company to benchmark.
    :param categories: Optional list of category IDs to run.  Defaults to all.
    :returns: Dict mapping category_id → list of check result dicts.
    """
    token = await acquire_access_token(company_id)
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if categories is None:
        categories = list(_CATEGORY_RUNNERS.keys())

    all_results: dict[str, list[dict[str, Any]]] = {}

    for category in categories:
        runner = _CATEGORY_RUNNERS.get(category)
        if runner is None:
            log_error("Unknown benchmark category", category=category)
            continue
        try:
            log_info("Running CIS benchmark", company_id=company_id, category=category)
            checks = await runner(token)
            all_results[category] = checks
            # Persist results
            for check in checks:
                await benchmark_repo.upsert_result(
                    company_id=company_id,
                    benchmark_category=category,
                    check_id=check["check_id"],
                    check_name=check["check_name"],
                    status=check["status"],
                    details=check.get("details") or "",
                    run_at=run_at,
                )
        except M365Error as exc:
            log_error(
                "CIS benchmark run failed",
                company_id=company_id,
                category=category,
                error=str(exc),
            )
            all_results[category] = [
                _unknown(
                    f"{category}_error",
                    "Benchmark run failed",
                    f"Could not run benchmarks: {exc}",
                )
            ]

    return all_results


async def get_last_results(company_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return the most recent stored benchmark results for the company.

    Groups results by category and adds remediation guidance to failed checks.
    Checks that have been excluded by an administrator are shown with
    ``STATUS_EXCLUDED`` and the exclusion reason, regardless of their raw result.

    Note: ``STATUS_EXCLUDED`` is an overlay applied at read time only.  It is
    **never** written to the ``cis_benchmark_results`` table (which enforces a
    CHECK constraint on the ``status`` column that does not include 'excluded').
    Exclusion data lives in the separate ``cis_benchmark_exclusions`` table.
    """
    rows = await benchmark_repo.list_results(company_id)
    exclusion_map = await benchmark_repo.get_exclusion_map(company_id)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        category = row["benchmark_category"]
        check_id = row["check_id"]
        exclusion_reason = exclusion_map.get(check_id)
        if exclusion_reason is not None:
            effective_status = STATUS_EXCLUDED
            details = f"Excluded: {exclusion_reason}" if exclusion_reason else "Excluded by administrator."
        else:
            effective_status = row["status"]
            details = row["details"]
        entry: dict[str, Any] = {
            "check_id": check_id,
            "check_name": row["check_name"],
            "status": effective_status,
            "raw_status": row["status"],
            "details": details,
            "run_at": row["run_at"],
            "exclusion_reason": exclusion_reason,
            "remediation": get_remediation(check_id) if effective_status == STATUS_FAIL else None,
        }
        by_category.setdefault(category, []).append(entry)
    return by_category


async def add_exclusion(company_id: int, check_id: str, reason: str) -> None:
    """Exclude a specific benchmark check for the given company."""
    await benchmark_repo.upsert_exclusion(
        company_id=company_id,
        check_id=check_id,
        reason=reason.strip(),
    )
    log_info("CIS benchmark check excluded", company_id=company_id, check_id=check_id)


async def remove_exclusion(company_id: int, check_id: str) -> None:
    """Remove a previously set exclusion for a benchmark check."""
    await benchmark_repo.delete_exclusion(company_id=company_id, check_id=check_id)
    log_info("CIS benchmark exclusion removed", company_id=company_id, check_id=check_id)


async def list_exclusions(company_id: int) -> list[dict[str, Any]]:
    """Return all active exclusions for the given company."""
    return await benchmark_repo.list_exclusions(company_id)
