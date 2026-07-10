# Tickets API

## Update reply time entry

`PATCH /api/tickets/{ticket_id}/replies/{reply_id}` updates the time tracking details for a specific conversation reply. Technicians with the `helpdesk.technician` permission (or super admins) can adjust the minutes recorded against a reply and toggle whether the work is billable. Requests must include a valid CSRF token.

### Request body

```json
{
  "minutes_spent": 30,
  "is_billable": true,
  "labour_type_id": 5
}
```

* `minutes_spent` – Optional integer between `0` and `1440`. Omit or send `null` to clear the stored duration.
* `is_billable` – Optional boolean indicating whether the minutes should count towards billable totals.
* `labour_type_id` – Optional integer referencing the labour code to apply to this reply. Omit or send `null` to remove the assigned labour type.

### Response

The endpoint returns the updated ticket and reply record. The reply payload includes the recalculated `time_summary` when time is recorded.

```json
{
  "ticket": {
    "id": 42,
    "subject": "Printer stopped working",
    "status": "open",
    "created_at": "2025-01-05T09:30:00Z",
    "updated_at": "2025-01-05T10:12:00Z",
    "priority": "normal",
    "category": null,
    "module_slug": null,
    "company_id": 7,
    "requester_id": 15,
    "assigned_user_id": 3,
    "external_reference": null
  },
  "reply": {
    "id": 128,
    "ticket_id": 42,
    "author_id": 3,
    "body": "<p>Replaced the toner and ran a test page.</p>",
    "is_internal": false,
    "minutes_spent": 30,
    "is_billable": true,
    "labour_type_id": 5,
    "labour_type_name": "Onsite support",
    "created_at": "2025-01-05T10:05:00Z",
    "time_summary": "30 minutes · Billable · Onsite support"
  }
}
```

### Error responses

* `404 Not Found` – Returned when the ticket or reply cannot be located, or the reply does not belong to the ticket.
* `400 Bad Request` – Returned when neither `minutes_spent`, `is_billable`, nor `labour_type_id` is provided, when `minutes_spent` falls outside the allowed range, or when the supplied `labour_type_id` does not exist.

### Notes

* Minute updates immediately refresh billable and non-billable totals in the technician workspace.
* Clients should include the `X-CSRF-Token` header with the value from the session cookie or `<meta name="csrf-token">` when making requests from the web UI.

## List ticket statuses

`GET /api/tickets/statuses` returns the available ticket statuses that technicians can assign in the helpdesk. The endpoint is available to authenticated users with the `helpdesk.technician` permission (super admins are implicitly granted access).

### Response

The response contains the canonical status slug (`techStatus`), the technician-facing label (`techLabel`), and the public label (`publicStatus`).

```json
{
  "statuses": [
    {
      "techStatus": "open",
      "techLabel": "Open",
      "publicStatus": "Open"
    },
    {
      "techStatus": "pending_vendor",
      "techLabel": "Pending vendor",
      "publicStatus": "Waiting on vendor"
    }
  ]
}
```

## Replace ticket statuses

`PUT /api/tickets/statuses` updates the catalogue of available ticket statuses. Only super admins may call this endpoint. Updates are applied transactionally—if a validation error occurs no changes are persisted.

### Request body

Provide an array of status objects. Each object must include a technician label (`techLabel`). Public labels default to the technician label when omitted. When renaming or adjusting an existing status include the `existingSlug` of the status being replaced.

```json
{
  "statuses": [
    {
      "techLabel": "Open",
      "publicStatus": "Open",
      "existingSlug": "open"
    },
    {
      "techLabel": "Pending vendor",
      "publicStatus": "Waiting on vendor"
    }
  ]
}
```

### Validation rules

* Technician labels must be unique once normalised to their slug form. Two labels that produce the same slug (for example, "Pending Vendor" and "pending_vendor") cannot exist simultaneously.
* Technician and public labels must be between 1 and 128 characters.
* Public labels may be duplicated, allowing multiple internal statuses to surface the same customer-facing label.

### Response

The endpoint returns the saved definitions using the technician and public labels provided. Slugs are derived automatically and always returned in lowercase snake case.

## Manage labour types

Labour types link ticket reply time entries to Xero product codes. Only super admins may create, update, or delete labour types. Technicians can retrieve the catalogue to populate UI selectors.

### List labour types

`GET /api/tickets/labour-types` returns the available labour codes.

```json
{
  "labour_types": [
    {
      "id": 5,
      "code": "ONSITE",
      "name": "Onsite support",
      "created_at": "2025-01-04T02:15:00Z",
      "updated_at": "2025-01-04T02:15:00Z"
    }
  ]
}
```

### Create a labour type

`POST /api/tickets/labour-types` creates a new labour code. Provide the `code` that matches the Xero product SKU and a descriptive `name`.

```json
{
  "code": "REMOTE",
  "name": "Remote support"
}
```

### Update a labour type

`PUT /api/tickets/labour-types/{labour_type_id}` updates the stored code or name. Fields are optional; omit values you do not wish to change.

```json
{
  "name": "Remote assistance"
}
```

### Delete a labour type

`DELETE /api/tickets/labour-types/{labour_type_id}` removes the labour type. Any ticket replies referencing the labour type are automatically reset to "No labour type".

## Manage ticket assets

Technicians can link one or more hardware assets to a helpdesk ticket to provide context for the issue being worked on. The asset selector appears directly below the **External reference** field on the ticket detail page. Linked assets are displayed in the ticket sidebar so other users can quickly review warranty status, serial numbers, and device metadata.

### Update ticket assets from the admin UI

`POST /admin/tickets/{ticket_id}` accepts an `assetIds` form field containing a list of asset identifiers that belong to the same company as the ticket. The field may be submitted multiple times (for example `assetIds=5&assetIds=8`) to attach several devices. If a ticket is not associated with a company the asset selector remains disabled to prevent mismatched links.

When processing the update the server verifies that every asset belongs to the ticket's company. Invalid identifiers return a `400 Bad Request` response with a descriptive validation error and no links are changed. Submitting an empty `assetIds` list clears all existing asset links for the ticket.

### Refresh available assets via the API

The admin UI uses `GET /api/companies/{company_id}/assets` to populate the multi-select when the ticket's company changes. The endpoint responds with an array of asset records sorted by name and includes Tactical RMM and Syncro identifiers where present. Each record contains:

* `id` – Asset primary key.
* `name` – Display name from the asset catalogue.
* `serial_number` – Optional serial number used for hardware tracking.
* `status` – Lifecycle status such as `active` or `retired`.
* `tactical_asset_id` / `syncro_asset_id` – Identifiers from upstream RMM systems.

Assets are only returned for companies that the authenticated user is authorised to manage. Requests from non-super admin accounts receive `403 Forbidden`.

## Search and reply from PowerShell

Automation scripts can search tickets and post replies with an API key. This is useful when a PowerShell script gathers diagnostic output and needs to add the result back to the matching helpdesk ticket.

### API key permissions

Create an API key in **Admin → API Keys** and scope it to only the endpoints the script needs:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tickets/` | Search for tickets by subject, description, or external reference. |
| `POST` | `/api/tickets/{ticket_id}/replies` | Add a public or internal reply to a ticket. |

Use IP restrictions where possible so the key only works from the automation host. Store the key outside the script, for example in an environment variable or a protected secret vault.

### Search by subject and post script output

The example below searches for the newest ticket matching a subject phrase, runs a local command, and posts the command output as a ticket reply. HTML is sent intentionally because ticket replies are rich text; command output is HTML-encoded before being wrapped in a `<pre>` block.

```powershell
$BaseUrl = "https://portal.example.com"
$ApiKey = $env:MYPORTAL_API_KEY
$Subject = "Device health check"

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "MYPORTAL_API_KEY is not set."
}

$Headers = @{
    "x-api-key" = $ApiKey
    "Accept" = "application/json"
}

$SearchUri = "{0}/api/tickets/?search={1}&limit=1" -f $BaseUrl, [uri]::EscapeDataString($Subject)
$SearchResult = Invoke-RestMethod -Method Get -Uri $SearchUri -Headers $Headers

if (-not $SearchResult.items -or $SearchResult.items.Count -eq 0) {
    throw "No ticket found for subject search: $Subject"
}

$Ticket = $SearchResult.items[0]

$ScriptOutput = & {
    hostname
    Get-Date -AsUTC
    Get-Service -Name Spooler | Format-List Name, Status, StartType | Out-String
} | Out-String

$EncodedOutput = [System.Net.WebUtility]::HtmlEncode($ScriptOutput)

$ReplyBody = @{
    body = "<p>Automated diagnostic results from $($env:COMPUTERNAME):</p><pre>$EncodedOutput</pre>"
    is_internal = $false
    minutes_spent = 0
    is_billable = $false
} | ConvertTo-Json -Depth 5

$ReplyHeaders = $Headers.Clone()
$ReplyHeaders["Content-Type"] = "application/json"

$ReplyUri = "{0}/api/tickets/{1}/replies" -f $BaseUrl, $Ticket.id
$Reply = Invoke-RestMethod -Method Post -Uri $ReplyUri -Headers $ReplyHeaders -Body $ReplyBody

"Posted reply {0} to ticket {1}: {2}" -f $Reply.reply.id, $Reply.ticket.id, $Reply.ticket.subject
```

### Notes

* API-key replies are treated as helpdesk replies and can set `is_internal`, `minutes_spent`, `is_billable`, and `labourTypeId`.
* Public replies may trigger configured downstream integrations for the ticket, such as chat or Trello synchronisation.
* The response includes the updated `ticket` object and created `reply` object, so scripts can log the reply ID for auditing.
