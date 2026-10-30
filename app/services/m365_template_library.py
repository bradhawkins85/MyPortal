"""Built-in Conditional Access and Intune compliance templates.

Importing this module registers each template as a Microsoft 365 policy
standard. A template's check looks for a MyPortal-named policy in the tenant
and compares the settings the template defines; its apply creates the policy
or puts the settings back.

Conditional Access policies are created in report-only mode and always
exclude the MyPortal break-glass accounts, so a template can never lock a
tenant out. Admins turn a policy On in Entra once its sign-in impact has been
reviewed; the check accepts either state.

Intune compliance policies are created assigned to all devices, marking a
device non-compliant after a 72 hour grace period.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.services import m365_standards as standards
from app.services.m365 import (
    M365Error,
    _graph_get_all,
    _graph_patch,
    _graph_post,
    acquire_access_token,
)
from app.services.m365_best_practices import (
    _CA_ADMIN_ROLE_TEMPLATES,
    _CA_POLICIES_URL,
    _MYPORTAL_CA_POLICY_PREFIX,
    _ROLE_TEMPLATE_DIRECTORY_SYNC,
    _SECURITY_DEFAULTS_URL,
    _myportal_break_glass_accounts,
    _safe_graph_get,
)

CA_CATEGORY = "Conditional Access templates"
INTUNE_CATEGORY = "Intune compliance templates"
REPORT_ONLY = "enabledForReportingButNotEnforced"
COMPLIANCE_POLICIES_URL = "https://graph.microsoft.com/v1.0/deviceManagement/deviceCompliancePolicies"
COMPLIANCE_GRACE_HOURS = 72
_MAX_LISTED = 5
_CA_EXCLUSIONS = ("excludeUsers", "excludeGroups", "excludeRoles")
_CA_IGNORED_PATHS = frozenset("conditions.users." + key for key in _CA_EXCLUSIONS)


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    payload: dict[str, Any]


def _all_users(**extra: Any) -> dict[str, Any]:
    return {"includeUsers": ["All"], "excludeRoles": [_ROLE_TEMPLATE_DIRECTORY_SYNC], **extra}


def _admins() -> dict[str, Any]:
    return {"includeRoles": list(dict.fromkeys(_CA_ADMIN_ROLE_TEMPLATES))}


def _ca(name: str, users: dict[str, Any], grant: dict[str, Any], **conditions: Any) -> dict[str, Any]:
    return {
        "displayName": _MYPORTAL_CA_POLICY_PREFIX + name,
        "conditions": {
            "users": users,
            "applications": conditions.pop("applications", {"includeApplications": ["All"]}),
            "clientAppTypes": ["all"],
            **conditions,
        },
        "grantControls": grant,
    }


def _grant(*controls: str, operator: str = "OR") -> dict[str, Any]:
    return {"operator": operator, "builtInControls": list(controls)}


CA_TEMPLATES: tuple[Template, ...] = (
    Template(
        "ca_template_guest_mfa",
        "Require MFA for guests and external users",
        "Guests and other external users must complete multifactor authentication to reach any app.",
        _ca("Require MFA for guests and external users", {
            "includeGuestsOrExternalUsers": {
                "guestOrExternalUserTypes": (
                    "internalGuest,b2bCollaborationGuest,b2bCollaborationMember,"
                    "b2bDirectConnectUser,otherExternalUser,serviceProvider"
                ),
                "externalTenants": {"membershipKind": "all"},
            },
        }, _grant("mfa")),
    ),
    Template(
        "ca_template_azure_management_mfa",
        "Require MFA for Azure management",
        "Everyone signing in to the Azure portal, Azure CLI or Azure PowerShell must complete multifactor authentication.",
        _ca("Require MFA for Azure management", _all_users(), _grant("mfa"),
            applications={"includeApplications": ["797f4846-ba00-4fd7-ba43-dac1f8f63013"]}),
    ),
    Template(
        "ca_template_block_device_code_flow",
        "Block device code sign-in",
        "Blocks the device code sign-in flow, which attackers use in phishing to take over accounts.",
        _ca("Block device code flow", _all_users(), _grant("block"),
            authenticationFlows={"transferMethods": "deviceCodeFlow"}),
    ),
    Template(
        "ca_template_block_unknown_platforms",
        "Block unknown device platforms",
        "Blocks sign-ins from device platforms other than Windows, macOS, iOS, Android and Linux.",
        _ca("Block unknown device platforms", _all_users(), _grant("block"), platforms={
            "includePlatforms": ["all"],
            "excludePlatforms": ["android", "iOS", "windows", "macOS", "linux"],
        }),
    ),
    Template(
        "ca_template_admin_compliant_device",
        "Require a managed device for administrators",
        "Administrators can only sign in from an Intune compliant or Microsoft Entra hybrid joined device.",
        _ca("Require managed device for administrators", _admins(),
            _grant("compliantDevice", "domainJoinedDevice")),
    ),
    Template(
        "ca_template_risky_signin_mfa",
        "Require MFA for risky sign-ins",
        "Sign-ins that Microsoft rates as medium or high risk must complete multifactor authentication. Needs Entra ID P2.",
        _ca("Require MFA for risky sign-ins", _all_users(), _grant("mfa"), signInRiskLevels=["high", "medium"]),
    ),
    Template(
        "ca_template_risky_user_password_change",
        "Require a password change for high-risk users",
        "Users whose account Microsoft rates as high risk must complete MFA and change their password. Needs Entra ID P2.",
        _ca("Require password change for high-risk users", _all_users(),
            _grant("mfa", "passwordChange", operator="AND"), userRiskLevels=["high"]),
    ),
)


def _compliance(odata_type: str, name: str, **settings: Any) -> dict[str, Any]:
    return {"@odata.type": f"#microsoft.graph.{odata_type}", "displayName": _MYPORTAL_CA_POLICY_PREFIX + name, **settings}


INTUNE_TEMPLATES: tuple[Template, ...] = (
    Template(
        "intune_template_windows_compliance",
        "Windows compliance baseline",
        "Windows devices need BitLocker, Secure Boot, code integrity, an active firewall, antivirus and Windows 10 22H2 or later.",
        _compliance(
            "windows10CompliancePolicy", "Windows compliance baseline",
            bitLockerEnabled=True, secureBootEnabled=True, codeIntegrityEnabled=True,
            activeFirewallRequired=True, antivirusRequired=True, antiSpywareRequired=True,
            defenderEnabled=True, osMinimumVersion="10.0.19045",
        ),
    ),
    Template(
        "intune_template_macos_compliance",
        "macOS compliance baseline",
        "Macs need FileVault, the firewall, System Integrity Protection, Gatekeeper and macOS 14 or later.",
        _compliance(
            "macOSCompliancePolicy", "macOS compliance baseline",
            storageRequireEncryption=True, firewallEnabled=True, systemIntegrityProtectionEnabled=True,
            gatekeeperAllowedAppSource="macAppStoreAndIdentifiedDevelopers", osMinimumVersion="14.0",
        ),
    ),
    Template(
        "intune_template_ios_compliance",
        "iOS and iPadOS compliance baseline",
        "iPhones and iPads need a six digit passcode that isn't simple, must not be jailbroken, and need iOS 17 or later.",
        _compliance(
            "iosCompliancePolicy", "iOS compliance baseline",
            passcodeRequired=True, passcodeMinimumLength=6, passcodeBlockSimple=True,
            securityBlockJailbrokenDevices=True, osMinimumVersion="17.0",
        ),
    ),
    Template(
        "intune_template_android_work_profile_compliance",
        "Android work profile compliance baseline",
        "Android devices with a work profile need a six character password and encryption, and must not be rooted.",
        _compliance(
            "androidWorkProfileCompliancePolicy", "Android work profile compliance baseline",
            passwordRequired=True, passwordMinimumLength=6, storageRequireEncryption=True,
            securityBlockJailbrokenDevices=True,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        parts = [part.strip().lower() for part in value.split(",")]
        return sorted(parts) if len(parts) > 1 else parts[0]
    if isinstance(value, list):
        return sorted(str(_normalise(item)) for item in value)
    return value


def differences(expected: dict[str, Any], actual: dict[str, Any] | None,
                ignored: frozenset[str] = frozenset(), prefix: str = "") -> list[str]:
    """Return the dotted paths where ``actual`` does not hold ``expected``'s values."""
    actual = actual if isinstance(actual, dict) else {}
    found: list[str] = []
    for key, value in expected.items():
        path = prefix + key
        if path in ignored or key.startswith("@odata"):
            continue
        if isinstance(value, dict):
            found.extend(differences(value, actual.get(key), ignored, path + "."))
        elif _normalise(value) != _normalise(actual.get(key)):
            found.append(path)
    return found


def _listed(names: list[str]) -> str:
    return ", ".join(names[:_MAX_LISTED]) + ("…" if len(names) > _MAX_LISTED else "")


def _by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in items if str(item.get("displayName") or "") == name), None)


def _item_url(base: str, item_id: Any) -> str:
    return base + "/" + quote(str(item_id), safe="")


# ---------------------------------------------------------------------------
# Conditional Access
# ---------------------------------------------------------------------------

def _ca_check(template: Template):
    async def check(company_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        name = template.payload["displayName"]
        try:
            token = await acquire_access_token(company_id, force_client_credentials=True)
            existing = _by_name(await _graph_get_all(token, _CA_POLICIES_URL), name)
        except M365Error as exc:
            return {"status": "unknown", "details": f"Unable to read Conditional Access policies: {exc}", "affected": []}
        if existing is None:
            return {"status": "fail", "details": f"The policy '{name}' is not in the tenant.", "affected": []}
        state = str(existing.get("state") or "")
        if state.lower() == "disabled":
            return {"status": "fail", "details": f"The policy '{name}' is turned off.", "affected": []}
        changed = differences(template.payload, existing, _CA_IGNORED_PATHS)
        if changed:
            return {"status": "fail", "details": f"The policy '{name}' has changed settings: {_listed(changed)}.",
                    "affected": changed}
        if state == REPORT_ONLY:
            return {"status": "pass", "affected": [], "details": (
                f"The policy '{name}' is in report-only mode. Turn it On in Entra once you have "
                "reviewed its sign-in impact."
            )}
        return {"status": "pass", "details": f"The policy '{name}' is On.", "affected": []}

    return check


def _ca_apply(template: Template):
    async def apply(company_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        name = template.payload["displayName"]
        try:
            token = await acquire_access_token(company_id, force_client_credentials=True)
            security_defaults = await _safe_graph_get(token, _SECURITY_DEFAULTS_URL)
            if security_defaults and security_defaults.get("isEnabled"):
                return {"status": "failed", "details": (
                    "Security Defaults are on, and Conditional Access policies can't run alongside them. "
                    "Turn Security Defaults off when you are ready to use Conditional Access."
                )}
            break_glass = await _myportal_break_glass_accounts(token)
            if not break_glass:
                return {"status": "failed", "details": (
                    "No MyPortal emergency access accounts were found to exclude. Run the "
                    "'Maintain 2–4 Global Administrators' remediation first, then try again."
                )}
            exclude_ids = [account["id"] for account in break_glass]
            existing = _by_name(await _graph_get_all(token, _CA_POLICIES_URL), name)
            payload = copy.deepcopy(template.payload)
            if existing is None:
                payload["state"] = REPORT_ONLY
                payload["conditions"]["users"]["excludeUsers"] = exclude_ids
                await _graph_post(token, _CA_POLICIES_URL, payload)
                return {"status": "remediated", "details": (
                    f"Created '{name}' in report-only mode, excluding the emergency access account(s)."
                )}
            current = (existing.get("conditions") or {}).get("users") or {}
            users = payload["conditions"]["users"]
            for key in _CA_EXCLUSIONS:
                merged = [*(current.get(key) or []), *(users.get(key) or [])]
                if key == "excludeUsers":
                    merged.extend(exclude_ids)
                if merged:
                    users[key] = list(dict.fromkeys(merged))
            update = {key: payload[key] for key in ("conditions", "grantControls") if key in payload}
            if str(existing.get("state") or "").lower() == "disabled":
                update["state"] = REPORT_ONLY
            await _graph_patch(token, _item_url(_CA_POLICIES_URL, existing["id"]), update)
        except M365Error as exc:
            return {"status": "failed", "details": f"Unable to update Conditional Access: {exc}"}
        return {"status": "remediated", "details": f"Put the template settings back on '{name}'."}

    return apply


# ---------------------------------------------------------------------------
# Intune compliance
# ---------------------------------------------------------------------------

async def _compliance_policies(token: str) -> list[dict[str, Any]]:
    return await _graph_get_all(token, COMPLIANCE_POLICIES_URL + "?$expand=assignments")


def _intune_check(template: Template):
    async def check(company_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        name = template.payload["displayName"]
        try:
            token = await acquire_access_token(company_id, force_client_credentials=True)
            existing = _by_name(await _compliance_policies(token), name)
        except M365Error as exc:
            return {"status": "unknown", "affected": [],
                    "details": f"Unable to read Intune compliance policies. The tenant may not have Intune: {exc}"}
        if existing is None:
            return {"status": "fail", "details": f"The compliance policy '{name}' is not in Intune.", "affected": []}
        changed = differences(template.payload, existing)
        if changed:
            return {"status": "fail", "details": f"The compliance policy '{name}' has changed settings: {_listed(changed)}.",
                    "affected": changed}
        if not existing.get("assignments"):
            return {"status": "fail", "details": f"The compliance policy '{name}' is not assigned to any devices.",
                    "affected": []}
        return {"status": "pass", "details": f"The compliance policy '{name}' is assigned and matches the template.",
                "affected": []}

    return check


def _intune_apply(template: Template):
    async def apply(company_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        name = template.payload["displayName"]
        try:
            token = await acquire_access_token(company_id, force_client_credentials=True)
            existing = _by_name(await _compliance_policies(token), name)
            if existing is None:
                payload = copy.deepcopy(template.payload)
                payload["scheduledActionsForRule"] = [{
                    "ruleName": "PasswordRequired",
                    "scheduledActionConfigurations": [{
                        "actionType": "block",
                        "gracePeriodHours": COMPLIANCE_GRACE_HOURS,
                        "notificationTemplateId": "",
                        "notificationMessageCCList": [],
                    }],
                }]
                existing = await _graph_post(token, COMPLIANCE_POLICIES_URL, payload)
                action = "Created"
            else:
                await _graph_patch(token, _item_url(COMPLIANCE_POLICIES_URL, existing["id"]), copy.deepcopy(template.payload))
                action = "Updated"
            if not existing.get("assignments"):
                await _graph_post(token, _item_url(COMPLIANCE_POLICIES_URL, existing["id"]) + "/assign", {
                    "assignments": [{"target": {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"}}],
                })
        except (M365Error, KeyError) as exc:
            return {"status": "failed", "details": f"Unable to update Intune: {exc}"}
        return {"status": "remediated", "details": f"{action} '{name}' and assigned it to all devices."}

    return apply


def register_templates() -> int:
    """Register every built-in template as a standard; returns how many."""
    count = 0
    for category, templates, make_check, make_apply in (
        (CA_CATEGORY, CA_TEMPLATES, _ca_check, _ca_apply),
        (INTUNE_CATEGORY, INTUNE_TEMPLATES, _intune_check, _intune_apply),
    ):
        for template in templates:
            if standards.get(template.id) is not None:
                continue
            standards.register(standards.Standard(
                id=template.id, name=template.name, category=category, description=template.description,
                check=make_check(template), apply=make_apply(template),
            ))
            count += 1
    return count


register_templates()
