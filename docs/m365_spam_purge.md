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

The tenant enterprise app must be able to use Security & Compliance PowerShell
and must be assigned the Purview **Compliance Search** and **Search And Purge**
roles. Exchange app-only access is also required for the Managed Folder
Assistant follow-up. Only authenticated super administrators and users with the
`helpdesk.technician` permission can access the UI or API.

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
`*.onmicrosoft.com` domain as the Purview routing organization while retaining
the tenant GUID in the InvokeCommand endpoint URL. This prevents the GUID from
being treated as an Exchange organization name and incorrectly routed to the
FFO test forest. The app registration therefore needs Microsoft Graph
`Domain.Read.All` application permission in addition to the Purview roles
above.
