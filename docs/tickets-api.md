# Tickets API

## Update reply time entry

`PATCH /api/tickets/{ticket_id}/replies/{reply_id}` updates the time tracking details for a specific conversation reply. Technicians with the `helpdesk.technician` permission (or super admins) can adjust the minutes recorded against a reply and toggle whether the work is billable. Requests must include a valid CSRF token.

### Request body

```json
{
  "minutes_spent": 30,
  "is_billable": true
}
```

* `minutes_spent` – Optional integer between `0` and `1440`. Omit or send `null` to clear the stored duration.
* `is_billable` – Optional boolean indicating whether the minutes should count towards billable totals.

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
    "created_at": "2025-01-05T10:05:00Z",
    "time_summary": "30 minutes · Billable"
  }
}
```

### Error responses

* `404 Not Found` – Returned when the ticket or reply cannot be located, or the reply does not belong to the ticket.
* `400 Bad Request` – Returned when neither `minutes_spent` nor `is_billable` is provided, or when `minutes_spent` falls outside the allowed range.

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
