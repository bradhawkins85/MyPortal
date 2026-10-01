# Staff onboarding requests API

Use this endpoint to submit a company-specific onboarding request for administrator approval. It
creates a request, not an active staff record, and uses the same configurable standard and custom
fields shown in that company's **New staff** form.

## Authentication and API-key scope

Send the API key in the `X-API-Key` header. If the key has endpoint permissions, grant it:

```text
POST /api/staff/requests
```

The target company is selected with the required `companyId` query parameter. Values from the JSON
body cannot override that company.

## Request fields

Standard fields use the JSON names below. A company's administrator can change which standard
fields are visible or required in the New staff form, so integrations should send every field the
company has configured as required.

| JSON field | Type | Notes |
| --- | --- | --- |
| `firstName` | string | Always required. |
| `lastName` | string | Always required. |
| `email` | string or `null` | Must be an email address when supplied. Required only when configured that way for the company. |
| `mobilePhone` | string or `null` | Mobile telephone number. |
| `department` | string or `null` | Department name or configured select value. |
| `dateOnboarded` | ISO 8601 date/time or `null` | A timezone offset is recommended, for example `2026-09-14T09:00:00+10:00`. |
| `enabled` | boolean | Defaults to `true` and is preserved when the request is approved. |
| `jobTitle` | string or `null` | Optional job title metadata. |
| `requestNotes` | string or `null` | Notes for the approving administrator. |
| `customFields` | object | Company-specific values keyed by the custom field's internal **name**, not its display label. |

Find the effective standard-field configuration in **Company settings → Staff fields** and custom
field names, types, and options in **Admin → Staff custom fields**. Select values must use the option
value rather than its label. Checkbox values are JSON booleans. Multiselect values may be supplied
as a JSON array (recommended) or a comma-separated string. Date custom fields use `YYYY-MM-DD`.
Unknown custom field names and invalid select options return `422 Unprocessable Entity`, with the
invalid field identified in `detail`.

## curl example

This example sets standard values and four common custom-field types. Replace the custom field names
and option values with those configured for the target company.

```bash
curl --request POST \
  "https://portal.example.com/api/staff/requests?companyId=42" \
  --header "X-API-Key: $MYPORTAL_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "firstName": "Taylor",
    "lastName": "Nguyen",
    "email": "taylor.nguyen@example.com",
    "mobilePhone": "+61 400 000 000",
    "department": "Finance",
    "dateOnboarded": "2026-09-14T09:00:00+10:00",
    "enabled": true,
    "jobTitle": "Financial Analyst",
    "requestNotes": "Requires access before the first morning.",
    "customFields": {
      "office_location": "sydney",
      "requires_laptop": true,
      "application_access": ["xero", "power-bi"],
      "probation_end_date": "2026-12-14"
    }
  }'
```

A successful call returns `201 Created`. The response contains `status: "pending"`, the selected
`company_id`, all saved standard values, and the `custom_fields` object. After an administrator
approves the request, those values are copied to the new staff record and its custom field values.
