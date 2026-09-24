# Microsoft 365 spam search and purge

The **Office 365 → Spam Search & Purge** workspace gives helpdesk technicians
and super administrators a reviewed workflow for removing malicious mail from
the currently selected customer tenant.

1. Enter a sender, subject, received-date range, and/or an advanced Purview
   `ContentMatchQuery` expression.
2. Run the non-destructive compliance search and monitor its live status.
3. Expand **Review** to inspect the exact query, match count, and Purview result.
4. Type `PURGE` and start the separate hard-delete action.

All timestamps are stored in UTC and localised by the browser. Request history,
matching and removal counts, raw result details, errors, and the initiating user
are retained. Successful purges also start Managed Folder Assistant for each
mailbox, matching the prior console workflow.

## Permissions

The tenant enterprise app needs **two separate application permission grants**,
even though both permissions are named `Exchange.ManageAsApp`. Granting one does
not grant the other.

| Resource | Resource application ID | Purpose |
| --- | --- | --- |
| Microsoft Exchange Online Protection | `00000007-0000-0ff1-ce00-000000000000` | Security & Compliance PowerShell |
| Office 365 Exchange Online | `00000002-0000-0ff1-ce00-000000000000` | Exchange Online PowerShell and the Managed Folder Assistant follow-up |

Microsoft documents the separate requirements for `Connect-IPPSSession` and
`Connect-ExchangeOnline` in its [app-only authentication guidance](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2).
MyPortal provisioning, reconnect repair, and permission diagnostics handle both
resource assignments independently. Only authenticated super administrators and
users with the `helpdesk.technician` permission can access the UI or API.

### Identify the tenant and application

Open the [Microsoft Entra admin center](https://entra.microsoft.com/), switch to
the tenant that MyPortal manages, and open **Enterprise applications → your
MyPortal application → Overview**. Record both identifiers:

- **Application ID** identifies the MyPortal app and should be used to
  distinguish similarly named apps.
- **Object ID** identifies the app's enterprise application (service principal)
  in this tenant.

### Check the existing grants

Within the enterprise application, open **Permissions** and inspect the
admin-consented application permissions. Confirm that `Exchange.ManageAsApp`
appears under **each** resource in the table above. A single entry is not enough
for the complete spam-purge workflow.

For an authoritative programmatic check, retrieve the service principal's app
role assignments:

```http
GET https://graph.microsoft.com/v1.0/servicePrincipals/{myportal-enterprise-app-object-id}/appRoleAssignments
```

These are the actual grants rather than only permissions requested on an app
registration. Recently changed assignments can take time to appear. See
[Microsoft Graph: list app role assignments granted to a service principal](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignments?view=graph-rest-1.0).

### Add missing permissions in the portal

If you own the app registration, go to **App registrations → your application →
API permissions**, then:

1. Select **Add a permission → APIs my organization uses**.
2. Search for **Microsoft Exchange Online Protection**.
3. Select **Application permissions → Exchange → Exchange.ManageAsApp**.
4. Select **Add permissions**.
5. Repeat these steps for **Office 365 Exchange Online** if its permission is
   missing.
6. Select **Grant admin consent for your tenant** while signed in with an
   authorised administrator account.
7. Verify that both entries show **Granted**.

For a vendor-owned, multitenant MyPortal app, the app registration may exist
only in the vendor's tenant. In that case, use MyPortal's provisioning or
reconnect consent flow, or use the tenant-side assignment method below.

### Provision or repair each resource assignment

Provisioning and reconnect repair must handle each resource application ID in
the table separately:

1. Find the resource's service principal in the customer tenant:

   ```http
   GET https://graph.microsoft.com/v1.0/servicePrincipals?$filter=appId eq '{resource-application-id}'&$select=id,displayName,appRoles
   ```

2. In its `appRoles`, locate the enabled role whose `value` is
   `Exchange.ManageAsApp` and whose `allowedMemberTypes` includes `Application`.
3. Check MyPortal's existing grants for both that resource's tenant-specific
   object ID and that role's ID.
4. If the assignment is absent, create it:

   ```http
   POST https://graph.microsoft.com/v1.0/servicePrincipals/{resource-service-principal-object-id}/appRoleAssignedTo
   Content-Type: application/json

   {
     "principalId": "{myportal-enterprise-app-object-id}",
     "resourceId": "{resource-service-principal-object-id}",
     "appRoleId": "{role-id-found-on-that-resource}"
   }
   ```

`resourceId` must be the resource's **service-principal object ID in the customer
tenant**, not the fixed resource application ID from the table.

Creating the assignment requires Microsoft Graph permissions such as
`AppRoleAssignment.ReadWrite.All` and `Application.Read.All`, plus an appropriate
administrator role when using delegated access. These permissions belong to the
identity performing the provisioning. See [Microsoft Graph: grant an app role
assignment](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-post-approleassignments?view=graph-rest-1.0).

### Verify roles and both connections

API permission grants are only one part of access. The app also needs the
administrative and RBAC roles required by the commands it executes. After a
repair, obtain fresh app sessions and test both `Connect-IPPSSession` and
`Connect-ExchangeOnline`, followed by an appropriate read-only command in each
session. See Microsoft's [authentication and role guidance](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2).

MyPortal diagnostics report two independent permission results. If Office 365
Exchange Online is granted but Microsoft Exchange Online Protection is missing,
the Managed Folder Assistant connection may work while Security & Compliance
access fails.

Microsoft's best-effort app-only eDiscovery setup also requires the enterprise
application service principal to be registered with `New-ServicePrincipal` and
added to the Purview `eDiscoveryManager` role group. An Entra Exchange
Administrator or Compliance Administrator assignment does not replace this
Purview RBAC setup and does not prove that the tenant's Purview organization has
finished provisioning.

The preflight verifies organization availability with `Get-OrganizationConfig`
and checks `eDiscoveryManager` membership before querying the service principal.
An unfiltered `Get-ServicePrincipal` request through Purview's REST endpoint can
return an internal `System.ArgumentNullException`, even when the application is
already registered. Membership is sufficient proof of registration; when a
separate lookup is necessary, MyPortal supplies the enterprise application
object ID as `Identity`. This prevents that Microsoft endpoint error from being
misreported as failures of every Purview prerequisite.

The preflight also verifies that the enterprise application itself has either
the **Exchange Administrator** or **Compliance Administrator** Entra directory
role. The diagnostics repair action assigns Compliance Administrator using the
delegated administrator session, then verifies the assignment before probing
Purview. Assigning the role to the interactive user instead of the enterprise
application does not satisfy app-only authentication.

## API

The authenticated endpoints are documented interactively at `/docs` under the
`Office365 Spam Purge` tag:

- `GET/POST /m365/spam-purge/api/requests`
- `GET/DELETE /m365/spam-purge/api/requests/{request_id}`
- `POST /m365/spam-purge/api/requests/{request_id}/search`
- `POST /m365/spam-purge/api/requests/{request_id}/purge`

Creating an API request produces a draft. Search and purge remain explicit,
separate API calls so an operator can review the completed result before any
destructive action. Calling the search endpoint again retries a failed search;
the web history also presents a **Retry search** action for failed requests.

Compliance commands resolve and use the tenant's initial
`*.onmicrosoft.com` domain in both the Purview InvokeCommand route and routing
header, matching the organization supplied to an app-only `Connect-IPPSSession`.
Using the tenant GUID in the route can cause Purview to look for a `CN` named
after that GUID in the FFO test forest. The app registration therefore needs
Microsoft Graph `Domain.Read.All` application permission in addition to the
Purview roles above. This permission is included in provisioning, automatic
permission repair, and M365 permission diagnostics.

## Compliance Administrator remediation

The spam search page can start an interactive Microsoft administrator sign-in to
assign the built-in **Compliance Administrator** directory role to the configured
enterprise application. The remediation resolves the enterprise application's
service principal by its application/client ID, verifies the authenticated tenant,
and creates a tenant-wide (`/`) assignment only when one does not already exist.

The delegated administrator session must have the Microsoft Graph
`RoleManagement.ReadWrite.Directory` scope consented and the signed-in account must
have an active supported role, such as Privileged Role Administrator. After Graph
verifies a new assignment, MyPortal reconnects and can queue the selected failed
search again. Role assignment success and the later Purview search result are
reported separately because the assignment does not establish Purview provisioning
or app-only command support.

The Microsoft Exchange Online Protection `Exchange.ManageAsApp` application
permission and administrator consent remain a separate prerequisite from the
Entra directory roles. MyPortal does not provision the tenant's Purview
organization or make Microsoft's best-effort app-only eDiscovery PowerShell
configuration a supported Microsoft API scenario.
