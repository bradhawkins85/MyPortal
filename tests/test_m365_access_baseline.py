from app.services.m365_access_baseline import (
    REQUIRED_DIRECTORY_ROLES,
    REQUIRED_PERMISSIONS,
)


def test_required_access_baseline_preserves_resource_type_and_name():
    entries = {(item.permission_type, item.resource, item.name) for item in REQUIRED_PERMISSIONS}
    # The 151-entry named snapshot is preserved and the formerly separate
    # provisioning/workload requirements are now additive contract entries.
    assert len(entries) == len(REQUIRED_PERMISSIONS) == 166
    assert ("Application", "Microsoft Graph", "AccessReview.Read.All") in entries
    assert ("Application", "Office 365 Exchange Online", "MailboxSettings.ReadWrite") in entries
    assert ("Delegated", "Office 365 Exchange Online", "MailboxSettings.ReadWrite") in entries
    assert ("Application", "Office 365 SharePoint Online", "Sites.FullControl.All") in entries
    assert ("Delegated", "Office 365 SharePoint Online", "AllSites.FullControl") in entries
    assert ("Delegated", "WindowsDefenderATP", "Vulnerability.Read") in entries
    assert ("Application", "Microsoft Graph", "Application.ReadWrite.OwnedBy") in entries
    assert ("Application", "Microsoft Exchange Online Protection", "Exchange.ManageAsApp") in entries
    assert all(item.feature and item.operation and item.disposition for item in REQUIRED_PERMISSIONS)


def test_required_directory_roles_are_exact():
    assert REQUIRED_DIRECTORY_ROLES == (
        "Cloud Application Administrator",
        "Reports Reader",
    )
