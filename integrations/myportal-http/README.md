# MyPortal HTTP API with curl

Use these `curl` examples to perform MyPortal Staff and Ticket operations over HTTP. MyPortal expects the API key in the `x-api-key` header and JSON request bodies for create and update operations. The shell expressions trim trailing slashes from the configured base URL.

Set these variables once before running the examples:

```bash
BASE_URL="https://portal.example.com"
API_KEY="replace-with-your-myportal-api-key"
```

All JSON examples use single quotes around the shell argument so the JSON is passed to MyPortal unchanged. Replace IDs, emails, company IDs, requester IDs, and assignee IDs with values from your portal.

### Staff actions

#### Get many staff

`companyId`, `email`, and `accountAction` are optional filters.

```bash
curl -sS -G "${BASE_URL%/}/api/staff" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "companyId=123" \
  --data-urlencode "email=jane.doe@example.com" \
  --data-urlencode "accountAction=onboard"
```

For the company-scoped polling fields that are available on the same endpoint, add filters such as `updatedAfter`, `cursor`, and `pageSize`:

```bash
curl -sS -G "${BASE_URL%/}/api/staff" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "companyId=123" \
  --data-urlencode "updatedAfter=2026-08-01T00:00:00Z" \
  --data-urlencode "pageSize=200"
```

#### Get one staff member

Retrieves one staff member by ID.

```bash
STAFF_ID=456

curl -sS "${BASE_URL%/}/api/staff/${STAFF_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

#### Create staff

Creates a staff member. Use camelCase JSON keys such as `companyId`, `firstName`, `lastName`, `mobilePhone`, `jobTitle`, `accountAction`, and `customFields`.

```bash
curl -sS -X POST "${BASE_URL%/}/api/staff" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "companyId": 123,
    "firstName": "Jane",
    "lastName": "Doe",
    "email": "jane.doe@example.com",
    "mobilePhone": "+15551234567",
    "enabled": true,
    "department": "Operations",
    "jobTitle": "Operations Manager",
    "accountAction": "onboard",
    "customFields": {
      "Employee Number": "E-10045"
    }
  }'
```

#### Update staff

Send only fields you want to change.

```bash
STAFF_ID=456

curl -sS -X PUT "${BASE_URL%/}/api/staff/${STAFF_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "mobilePhone": "+15557654321",
    "department": "Service Desk",
    "jobTitle": "Senior Technician",
    "enabled": true,
    "customFields": {
      "Employee Number": "E-10045"
    }
  }'
```

#### Delete staff

MyPortal returns `204 No Content` on success.

```bash
STAFF_ID=456

curl -sS -i -X DELETE "${BASE_URL%/}/api/staff/${STAFF_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

### Ticket actions

#### Get many tickets

The `/api/tickets/` endpoint accepts optional `status`, `company_id`, `assigned_user_id`, `search`, and `limit` query parameters.

```bash
curl -sS -G "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "status=open" \
  --data-urlencode "company_id=123" \
  --data-urlencode "assigned_user_id=789" \
  --data-urlencode "search=printer offline" \
  --data-urlencode "limit=50"
```

The API returns a wrapper object for ticket searches. Use `jq '.items[]'` for one-record-at-a-time output:

```bash
curl -sS -G "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "search=printer offline" \
  --data-urlencode "limit=50" | jq '.items[]'
```

#### Get one ticket

Retrieves one ticket by ID.

```bash
TICKET_ID=1001

curl -sS "${BASE_URL%/}/api/tickets/${TICKET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

#### Create ticket

When using API-key authentication, `requester_id` is required.

```bash
curl -sS -X POST "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "subject": "Printer offline",
    "description": "The reception printer is offline and users cannot print.",
    "status": "open",
    "priority": "normal",
    "requester_id": 456,
    "company_id": 123,
    "assigned_user_id": 789
  }'
```

#### Update ticket

Send only fields you want to change.

```bash
TICKET_ID=1001

curl -sS -X PUT "${BASE_URL%/}/api/tickets/${TICKET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "status": "in_progress",
    "priority": "high",
    "assigned_user_id": 789,
    "description": "Updated ticket description from external automation."
  }'
```

#### Delete ticket

MyPortal returns `204 No Content` on success.

```bash
TICKET_ID=1001

curl -sS -i -X DELETE "${BASE_URL%/}/api/tickets/${TICKET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

### Raw JSON body equivalents

Include any advanced fields directly in the JSON payload. For example, to create a ticket with additional API fields:

```bash
curl -sS -X POST "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "subject": "Quarterly access review",
    "description": "Please complete the quarterly review.",
    "requester_id": 456,
    "company_id": 123,
    "category": "security",
    "module_slug": "compliance",
    "external_reference": "quarterly-review-2026-08"
  }'
```
