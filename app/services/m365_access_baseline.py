"""Authoritative Microsoft 365 access baseline for the MyPortal enterprise app."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredPermission:
    resource: str
    permission_type: str
    name: str


RESOURCE_APP_IDS = {
    "Microsoft Graph": "00000003-0000-0000-c000-000000000000",
    "Office 365 Exchange Online": "00000002-0000-0ff1-ce00-000000000000",
    "Office 365 SharePoint Online": "00000003-0000-0ff1-ce00-000000000000",
    "Office 365 Management APIs": "c5393580-f805-4401-95e8-94b7a6ef2fc2",
    "Skype and Teams Tenant Admin API": "48ac35b8-9aa8-4d74-927d-1f4a14a0b239",
    "WindowsDefenderATP": "fc780465-2017-40d4-a0c5-307022471b92",
    # This is a tenant-specific API.  It is deliberately resolved by display name.
    "M365 License Manager": None,
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


REQUIRED_PERMISSIONS = tuple(
    RequiredPermission(resource, permission_type, name)
    for permission_type, groups in (("Application", _APPLICATION), ("Delegated", _DELEGATED))
    for resource, names in groups.items()
    for name in names.split()
)


def permissions_by_resource() -> dict[str, list[RequiredPermission]]:
    grouped: dict[str, list[RequiredPermission]] = {}
    for permission in REQUIRED_PERMISSIONS:
        grouped.setdefault(permission.resource, []).append(permission)
    return grouped
