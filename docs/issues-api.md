# Issue Tracker API

The Issue Tracker API exposes CRUD-style endpoints that allow technicians and automation clients to manage shared issues and the status of each company assignment. All endpoints require an authenticated session with helpdesk technician access or super-admin privileges. Requests must include a valid CSRF token when invoked from browser contexts.

## Base Path

```
/api/issues
```

## Authentication

These endpoints require a valid session cookie. API clients should authenticate using the standard session workflow before making requests. Responses will return HTTP 401 if the session is missing or expired, and HTTP 403 if the user lacks helpdesk technician permissions.

## List issues

```
GET /api/issues
```

Retrieves issues with their associated company assignments. Optional query parameters:

| Query        | Type   | Description                                           |
|--------------|--------|-------------------------------------------------------|
| `search`     | str    | Case-insensitive search across issue names/descriptions. |
| `status`     | str    | Filter assignments by status (`new`, `investigating`, `in_progress`, `monitoring`, `resolved`, `closed`). |
| `companyId`  | int    | Filter by company identifier.                         |
| `companyName`| str    | Filter by company name (case-insensitive).            |

Example response:

```json
{
  "items": [
    {
      "name": "Network degradation",
      "description": "Packet loss on primary WAN links",
      "created_at": "2025-01-04T10:15:00+00:00",
      "created_at_iso": "2025-01-04T10:15:00+00:00",
      "updated_at": "2025-01-09T08:45:00+00:00",
      "updated_at_iso": "2025-01-09T08:45:00+00:00",
      "assignments": [
        {
          "assignment_id": 12,
          "issue_id": 7,
          "company_id": 3,
          "company_name": "Acme Manufacturing",
          "status": "investigating",
          "status_label": "Investigating",
          "updated_at": "2025-01-09T08:45:00+00:00",
          "updated_at_iso": "2025-01-09T08:45:00+00:00"
        }
      ]
    }
  ],
  "total": 1
}
```

## Create an issue

```
POST /api/issues
```

Request body:

```json
{
  "name": "Printer firmware failure",
  "description": "Firmware 4.3.2 fails to initialise on multi-function devices.",
  "companies": [
    {"company_name": "Contoso", "status": "new"},
    {"company_name": "Fabrikam", "status": "investigating"}
  ]
}
```

Response returns the created issue with assignments. Duplicate names return HTTP 400.

## Update an issue

```
PUT /api/issues/{issue_name}
```

Parameters:

- `issue_name`: Current name of the issue to update (case-insensitive).

Request body properties:

| Field            | Type                | Description                                        |
|------------------|---------------------|----------------------------------------------------|
| `description`    | str | null         | Optional new description.                          |
| `new_name`       | str | null         | Rename the issue (must remain unique).             |
| `add_companies`  | list[object]        | Optional collection of company assignments to add. Each object accepts `company_name` and `status`. |

## Update assignment status

```
PUT /api/issues/status
```

The Issue Tracker requires the issue name and company name for updates so that API clients can work with natural identifiers.

Request body:

```json
{
  "issue_name": "Network degradation",
  "company_name": "Acme Manufacturing",
  "status": "resolved"
}
```

Returns the updated assignment payload. If the company was not previously assigned, the endpoint creates the assignment automatically with the provided status.

## Error handling

- `400 Bad Request` — validation failed or an operation could not be completed.
- `404 Not Found` — issue or company could not be located.
- `422 Unprocessable Entity` — invalid query parameter (for example, an unsupported status filter).

All timestamps are returned in ISO 8601 (UTC). Display layers should convert to the user’s local timezone when presenting values to end users.
