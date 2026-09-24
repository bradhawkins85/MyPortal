"""Authoritative Microsoft 365 access baseline for the MyPortal enterprise app."""

from __future__ import annotations

from dataclasses import dataclass


CONTRACT_VERSION = "2026-09-24.3"


@dataclass(frozen=True)
class RequiredPermission:
    """One versioned permission requirement used by every M365 workflow.

    ``permission_id`` is populated for stable, verified identifiers and otherwise
    resolved from the tenant resource service principal.  This is intentional:
    sovereign and tenant-owned APIs need not expose the same identifier.
    """

    resource: str
    permission_type: str
    name: str
    permission_id: str | None = None
    feature: str = "legacy_baseline"
    operation: str = "Existing MyPortal Microsoft 365 integration operation"
    access: str = "read"
    licensing: str = "Microsoft 365 workload/API availability"
    consent_authority: str = "Microsoft Entra administrator"
    authentication_modes: tuple[str, ...] = ("delegated",)
    disposition: str = "legacy"


RESOURCE_APP_IDS = {
    "Microsoft Graph": "00000003-0000-0000-c000-000000000000",
    "Office 365 Exchange Online": "00000002-0000-0ff1-ce00-000000000000",
    "Office 365 SharePoint Online": "00000003-0000-0ff1-ce00-000000000000",
    "Office 365 Management APIs": "c5393580-f805-4401-95e8-94b7a6ef2fc2",
    "Skype and Teams Tenant Admin API": "48ac35b8-9aa8-4d74-927d-1f4a14a0b239",
    "WindowsDefenderATP": "fc780465-2017-40d4-a0c5-307022471b92",
    # Tenant-owned resource. Its configured app ID must be verified at runtime;
    # display name alone is never treated as identity.
    "M365 License Manager": None,
    "Microsoft Exchange Online Protection": "00000007-0000-0ff1-ce00-000000000000",
}

REQUIRED_DIRECTORY_ROLES = (
    "Cloud Application Administrator",
    "Reports Reader",
)

_APPLICATION = {
    "Office 365 Exchange Online": "MailboxSettings.ReadWrite Calendars.ReadWrite.All Exchange.ManageAsApp",
    "Microsoft Graph": """Place.Read.All RoleEligibilitySchedule.Read.Directory Policy.Read.DeviceConfiguration
DeviceManagementManagedDevices.Read.All Policy.ReadWrite.ConditionalAccess Policy.ReadWrite.AuthenticationMethod
TeamMember.ReadWriteNonOwnerRole.All SharePointTenantSettings.ReadWrite.All Device.ReadWrite.All User.ReadWrite.All
Policy.ReadWrite.AuthenticationFlows Domain.ReadWrite.All ReportSettings.ReadWrite.All SecurityEvents.Read.All
UserAuthenticationMethod.ReadWrite.All RoleManagement.Read.Directory Policy.ReadWrite.ApplicationConfiguration
Channel.ReadBasic.All SecurityAlert.Read.All Application.ReadWrite.All Group.Read.All Directory.ReadWrite.All
Policy.ReadWrite.ConsentRequest CrossTenantInformation.ReadBasic.All Sites.Read.All
DeviceManagementServiceConfig.Read.All PeopleSettings.ReadWrite.All Group.Create Group.ReadWrite.All
Files.ReadWrite.All Directory.Read.All Domain.Read.All ChannelMember.Read.All
OrgSettings-AppsAndServices.ReadWrite.All DeviceManagementServiceConfig.ReadWrite.All SecurityIncident.Read.All
TeamMember.ReadWrite.All SecurityIncident.ReadWrite.All RoleManagementPolicy.Read.Directory
DeviceManagementRBAC.Read.All Organization.ReadWrite.All DeviceManagementManagedDevices.ReadWrite.All
AccessReview.Read.All Mail.Send ChannelMember.ReadWrite.All GroupMember.ReadWrite.All AuditLog.Read.All
Channel.Create Policy.Read.All Policy.ReadWrite.CrossTenantAccess DeviceManagementConfiguration.ReadWrite.All
DeviceManagementManagedDevices.PrivilegedOperations.All Sites.FullControl.All Policy.ReadWrite.Authorization
DeviceManagementApps.ReadWrite.All RoleAssignmentSchedule.Read.Directory Reports.Read.All
OrgSettings-Forms.ReadWrite.All DeviceManagementRBAC.ReadWrite.All PrivilegedAccess.ReadWrite.AzureADGroup""",
    "Office 365 SharePoint Online": "Sites.FullControl.All",
    "WindowsDefenderATP": "Vulnerability.Read.All",
}

_DELEGATED = {
    "Office 365 Management APIs": "ActivityFeed.Read",
    "M365 License Manager": "LicenseManager.AccessAsUser",
    "Office 365 Exchange Online": "Exchange.Manage Calendars.ReadWrite.All MailboxSettings.ReadWrite",
    "Microsoft Graph": """SharePointTenantSettings.ReadWrite.All UnifiedGroupMember.Read.AsGuest
RoleManagement.ReadWrite.Directory Organization.ReadWrite.All SecurityActions.ReadWrite.All offline_access
Policy.ReadWrite.ConditionalAccess Policy.Read.All AuditLog.Read.All DeviceManagementConfiguration.ReadWrite.All
DeviceManagementApps.ReadWrite.All DeviceManagementRBAC.ReadWrite.All DeviceManagementManagedDevices.ReadWrite.All
DeviceManagementServiceConfig.ReadWrite.All SecurityEvents.ReadWrite.All Device.Command Device.Read Reports.Read.All
Sites.ReadWrite.All Mail.Send.Shared User.ReadWrite.All Group.ReadWrite.All Directory.AccessAsUser.All Mail.Send
openid profile Member.Read.Hidden TeamsActivity.Read PrivilegedAccess.ReadWrite.AzureResources
PrivilegedAccess.Read.AzureResources ChannelMessage.Edit ChannelMessage.Send Application.ReadWrite.All
GroupMember.ReadWrite.All ThreatAssessment.ReadWrite.All UserAuthenticationMethod.ReadWrite.All
UserAuthenticationMethod.ReadWrite UserAuthenticationMethod.Read.All TeamsTab.Create TeamsTab.ReadWrite.All
Domain.Read.All Device.Read.All User.ManageIdentities.All Channel.Create Channel.Delete.All
ChannelSettings.Read.All ChannelSettings.ReadWrite.All Team.ReadBasic.All Channel.ReadBasic.All
TeamSettings.Read.All TeamSettings.ReadWrite.All TeamMember.ReadWrite.All ConsentRequest.Read.All
Policy.ReadWrite.ConsentRequest ChannelMember.Read.All ChannelMember.ReadWrite.All
Policy.ReadWrite.AuthenticationFlows ChannelMessage.Read.All Policy.ReadWrite.AuthenticationMethod
Policy.ReadWrite.Authorization Policy.ReadWrite.DeviceConfiguration Team.Create TeamMember.ReadWriteNonOwnerRole.All
ServiceMessage.Read.All ServiceHealth.Read.All SecurityIncident.ReadWrite.All
Policy.ReadWrite.ApplicationConfiguration DeviceManagementManagedDevices.PrivilegedOperations.All
ReportSettings.ReadWrite.All BitlockerKey.Read.All AppRoleAssignment.ReadWrite.All DeviceLocalCredential.Read.All
DelegatedAdminRelationship.ReadWrite.All Place.ReadWrite.All PeopleSettings.ReadWrite.All
IdentityRiskEvent.ReadWrite.All IdentityRiskyServicePrincipal.ReadWrite.All IdentityRiskyUser.ReadWrite.All""",
    "Office 365 SharePoint Online": "AllSites.FullControl",
    "Skype and Teams Tenant Admin API": "user_impersonation",
    "WindowsDefenderATP": "Vulnerability.Read",
}


# Stable Graph IDs already used by provisioning before this contract existed.
# Keeping this snapshot here prevents provisioning and diagnostics drifting apart.
GRAPH_APPLICATION_PERMISSION_IDS = {
    "User.Read.All": "df021288-bdef-4463-88db-98f22de89214",
    "User.ReadWrite.All": "741f803b-c850-494e-b5df-cde7c675a1ca",
    # Staff lifecycle permissions are additive.  User.ReadWrite.All remains in
    # the legacy baseline for compatibility, but it is not presented as the
    # least-privileged permission for these property-specific operations.
    "User-PasswordProfile.ReadWrite.All": "4c37e1b6-35a1-43bf-926a-6f30f2cdf585",
    "User.EnableDisableAccount.All": "3011c876-62b7-4ada-afa2-506cbbecc68c",
    "User.RevokeSessions.All": "77f952ba-9a5f-4521-8c9d-6c9648f7eaf7",
    "LicenseAssignment.ReadWrite.All": "5facf0c1-8979-4e95-abcf-ff3d079771c0",
    "RoleManagement.ReadWrite.Directory": "9e3f62cf-ca93-4989-b6ce-bf83c28f9fe8",
    "Directory.Read.All": "7ab1d382-f21e-4acd-a863-ba3e13f7da61",
    "Application.ReadWrite.OwnedBy": "18a4783c-866b-4cc7-a460-3d5e5662c884",
    "Policy.Read.All": "246dd0d5-5bd0-4def-940b-0421030a5b68",
    "Policy.ReadWrite.AuthenticationMethod": "29c18626-4985-4dcd-85c0-193eef327366",
    "Policy.ReadWrite.Authorization": "fb221be6-99f2-473f-bd32-01c6a0e9ca3b",
    "Organization.Read.All": "498476ce-e0fe-48b0-b801-37ba7e2685c6",
    "Domain.Read.All": "dbb9058a-0e50-45d7-ae91-66909b5d4664",
    "DeviceManagementConfiguration.Read.All": "dc377aa6-52d8-4e23-b271-2a7ae04cedf3",
    "DeviceManagementManagedDevices.Read.All": "2f51be20-0bb4-4fed-bf7b-db946066c75e",
    "AuditLog.Read.All": "b0afded3-3588-46d8-8b3d-9842eff778da",
    "Reports.Read.All": "230c1aed-a721-4c5d-9cb4-a90514e508ef",
    "MailboxSettings.Read": "40f97065-369a-49f4-947c-6a255697ae91",
    "MailboxSettings.ReadWrite": "6931bccd-447a-43d1-b442-00a195474933",
    "ReportSettings.ReadWrite.All": "ee353f83-55ef-4b78-82da-555bfa2b4b95",
    "Mail.ReadWrite": "e2a3a72e-5f79-4c64-b1b1-878b674786c9",
    "GroupMember.ReadWrite.All": "dbaae8cf-10b5-4b86-a4a1-f871c94c6695",
    "IdentityRiskyUser.Read.All": "dc5007c0-2d7d-4c42-879c-2dab87571379",
    "Application.Read.All": "9a5d68dd-52b0-4cc2-bd40-abcf44ac3a30",
    "SecurityEvents.Read.All": "bf394140-e372-4bf9-a898-299cfc7564e5",
    "SecuritySecureScore.Read.All": "e0b77adb-e790-44a3-b0a0-257d06303687",
    "SharePointTenantSettings.ReadWrite.All": "19b94e34-907c-4f43-bde9-38b1909ed408",
    "Sites.Read.All": "332a536c-c7ef-4017-ab91-336970924f0d",
    "Sites.ReadWrite.All": "9492366f-7969-46a4-8d15-ed1a20078fff",
    "Group.ReadWrite.All": "62a82d76-70ea-41e2-9197-370581804d09",
    "UserAuthenticationMethod.Read.All": "38d9df27-64da-44fd-b7c5-a6fbac20248f",
    "OrgSettings-Forms.ReadWrite.All": "2cb92fee-97a3-4034-8702-24a6f5d0d1e9",
}

# Permissions from the legacy provisioning inventory that the named baseline
# omitted. They remain explicit and additive for compatibility.
_PROVISION_ONLY = {
    "Application.ReadWrite.OwnedBy": ("credential_rotation", "POST /applications/{id}/addPassword", "write", "bootstrap-only"),
    "Organization.Read.All": ("tenant_inventory", "GET /organization", "read", "required"),
    "MailboxSettings.Read": ("mailbox_reporting", "GET /users/{id}/mailboxSettings", "read", "required"),
    "Mail.ReadWrite": ("mail_import", "GET/PATCH /users/{id}/messages", "write", "optional"),
    "IdentityRiskyUser.Read.All": ("security_monitoring", "GET /identityProtection/riskyUsers", "read", "optional"),
    "Application.Read.All": ("application_monitoring", "GET /applications", "read", "required"),
    "SecuritySecureScore.Read.All": ("security_monitoring", "GET /security/secureScores", "read", "optional"),
    "Sites.ReadWrite.All": ("offboarding_export", "PUT /sites/{id}/drive/items", "write", "optional"),
}

_STAFF_LIFECYCLE = {
    "User-PasswordProfile.ReadWrite.All": ("password_reset", "PATCH /users/{id} passwordProfile", "write", "required"),
    "User.EnableDisableAccount.All": ("account_status", "PATCH /users/{id} accountEnabled (with User.Read.All)", "write", "required"),
    "User.RevokeSessions.All": ("session_revocation", "POST /users/{id}/revokeSignInSessions", "write", "required"),
    "LicenseAssignment.ReadWrite.All": ("license_assignment", "POST /users/{id}/assignLicense (with User.Read.All)", "write", "required"),
}


def _metadata(resource: str, permission_type: str, name: str) -> tuple[str, str, str, str, tuple[str, ...]]:
    feature = "legacy_baseline"
    operation = {
        "Microsoft Graph": "Microsoft Graph endpoint documented by the permission name",
        "Office 365 Exchange Online": "Exchange Online PowerShell cmdlet / Outlook workload API",
        "Office 365 SharePoint Online": "SharePoint site API operation",
        "Office 365 Management APIs": "GET /api/v1.0/{tenant}/activity/feed/subscriptions/content",
        "Skype and Teams Tenant Admin API": "Teams PowerShell tenant administration cmdlet",
        "WindowsDefenderATP": "GET /api/vulnerabilities",
        "M365 License Manager": "License Manager configured API operation",
    }[resource]
    access = "write" if any(token in name for token in ("Write", "Manage", "Create", "Send", "Command", "Edit", "Delete", "FullControl")) else "read"
    disposition = "legacy"
    modes = ("application",) if permission_type == "Application" else ("delegated",)
    if resource == "M365 License Manager":
        feature, disposition = "license_management", "optional"
    return feature, operation, access, disposition, modes


def _entry(resource: str, permission_type: str, name: str) -> RequiredPermission:
    feature, operation, access, disposition, modes = _metadata(resource, permission_type, name)
    if resource == "Microsoft Graph" and permission_type == "Application" and name in _PROVISION_ONLY:
        feature, operation, access, disposition = _PROVISION_ONLY[name]
    if resource == "Microsoft Graph" and permission_type == "Application" and name in _STAFF_LIFECYCLE:
        feature, operation, access, disposition = _STAFF_LIFECYCLE[name]
    permission_id = GRAPH_APPLICATION_PERMISSION_IDS.get(name) if resource == "Microsoft Graph" and permission_type == "Application" else None
    return RequiredPermission(resource, permission_type, name, permission_id, feature, operation, access,
                              "Workload licence required when the endpoint is licensed",
                              "Tenant administrator/admin consent", modes, disposition)


_named_baseline = [
    _entry(resource, permission_type, name)
    for permission_type, groups in (("Application", _APPLICATION), ("Delegated", _DELEGATED))
    for resource, names in groups.items()
    for name in names.split()
]
for name in GRAPH_APPLICATION_PERMISSION_IDS:
    key = ("Microsoft Graph", "Application", name)
    if not any((p.resource, p.permission_type, p.name) == key for p in _named_baseline):
        _named_baseline.append(_entry(*key))

# Non-Graph app roles used by workload cmdlets are contract entries too.
_named_baseline.extend((
    RequiredPermission("Microsoft Exchange Online Protection", "Application", "Exchange.ManageAsApp",
                       "dc50a0fb-09a3-484d-be87-e023b12c6440", "purview", "Get-ProtectionAlert/New-ProtectionAlert",
                       "write", "Purview licensing", "Tenant administrator plus Compliance Administrator RBAC", ("application",), "required"),
    # Teams PowerShell application authentication intentionally has no app role
    # on the legacy Skype/Teams API.  The supported flow uses Graph
    # Organization.Read.All plus Teams RBAC and two resource tokens.
))

REQUIRED_PERMISSIONS = tuple(_named_baseline)
PERMISSION_CONTRACT = REQUIRED_PERMISSIONS


@dataclass(frozen=True)
class WorkloadRbacRequirement:
    feature: str
    resource: str
    role: str
    operation: str
    disposition: str = "required"


WORKLOAD_RBAC_REQUIREMENTS = (
    WorkloadRbacRequirement("provisioning", "Microsoft Entra", "Cloud Application Administrator", "Create app and grant consent", "bootstrap-only"),
    WorkloadRbacRequirement("reporting", "Microsoft Entra", "Reports Reader", "Read tenant reports"),
    WorkloadRbacRequirement("exchange_administration", "Exchange Online", "Exchange Administrator", "Exchange Online PowerShell cmdlets"),
    WorkloadRbacRequirement("teams_administration", "Microsoft Teams", "Teams Service Administrator", "Teams PowerShell cmdlets"),
    WorkloadRbacRequirement("purview", "Microsoft Purview", "Compliance Administrator/eDiscoveryManager", "Protection and compliance cmdlets"),
)


def permissions_by_resource(*, features: set[str] | None = None, include_legacy: bool = True) -> dict[str, list[RequiredPermission]]:
    """Return the contract inventory, optionally narrowed to enabled features."""
    grouped: dict[str, list[RequiredPermission]] = {}
    for permission in PERMISSION_CONTRACT:
        if features is not None and permission.feature not in features and not (include_legacy and permission.disposition == "legacy"):
            continue
        grouped.setdefault(permission.resource, []).append(permission)
    return grouped


def permission_contract_snapshot() -> tuple[dict[str, object], ...]:
    """Stable serialisable inventory used by manifest, diagnostics and tests."""
    return tuple({field: getattr(item, field) for field in item.__dataclass_fields__} for item in PERMISSION_CONTRACT)


def consent_delta(
    configured: set[tuple[str, str, str]], *, features: set[str] | None = None
) -> tuple[RequiredPermission, ...]:
    """Return only contract permissions absent from a manifest snapshot."""
    return tuple(
        permission
        for permissions in permissions_by_resource(features=features).values()
        for permission in permissions
        if (permission.resource, permission.permission_type, permission.name) not in configured
    )


def setup_explanation(*, features: set[str] | None = None) -> tuple[dict[str, object], ...]:
    """Generate guided consent/resource/RBAC steps from the same contract."""
    steps: list[dict[str, object]] = []
    for resource, permissions in permissions_by_resource(features=features).items():
        steps.append({
            "kind": "admin_consent",
            "resource": resource,
            "resource_app_id": RESOURCE_APP_IDS[resource],
            "permissions": tuple(permission.name for permission in permissions),
            "authority": tuple(sorted({permission.consent_authority for permission in permissions})),
        })
    selected = features
    for requirement in WORKLOAD_RBAC_REQUIREMENTS:
        if selected is None or requirement.feature in selected:
            steps.append({
                "kind": "workload_rbac",
                "resource": requirement.resource,
                "role": requirement.role,
                "operation": requirement.operation,
            })
    return tuple(steps)
