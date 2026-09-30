# Staff workflow webhooks API

A **Pause For Webhook** step in an onboarding or offboarding workflow stops the workflow until an
external script calls back. This lets a script do work MyPortal cannot, such as creating an account
on an on-premises domain controller, and then hand results back to the workflow.

Typical onboarding flow:

1. A staff member submits a new staff request.
2. A technician approves the request.
3. The workflow runs until it reaches the Pause For Webhook step and waits.
4. The external script lists the paused workflows, including every field captured by the request.
5. The script does its work and calls the resume webhook with any values to return (for example a
   generated password or a pass/fail status).
6. MyPortal resumes the remaining steps (for example adding the user to Microsoft 365 groups).
7. The workflow completes and no longer appears in the pending list.

## Credentials

Each Pause For Webhook step has its own webhook URL and POST key. Copy both from the step in the
company's workflow editor. They are stable for that company, direction, workflow and step name, so a
script can be configured once. Treat the POST key as a secret: it both lists and resumes workflows.

## List paused workflows

```text
GET /api/staff/workflow-webhooks/{webhookId}/pending
X-Webhook-Post-Key: <post key>
```

Optional query parameter `limit` (1-500, default 100). Returns the workflows currently paused on this
step, oldest first. A wrong POST key returns `403`; an empty array means nothing is waiting.

```json
[
  {
    "executionId": 88,
    "companyId": 9,
    "companyName": "Example Pty Ltd",
    "staffId": 1001,
    "direction": "onboarding",
    "workflowKey": "staff_onboarding_m365",
    "state": "waiting_external",
    "stepName": "Create AD account",
    "pausedAt": "2026-09-30T01:00:00",
    "requestedAt": "2026-09-29T23:00:00",
    "resumeUrl": "https://portal.example.com/api/staff/workflow-webhooks/{webhookId}",
    "staff": {
      "id": 1001,
      "firstName": "Jane",
      "lastName": "Starter",
      "email": "jane.starter@example.com",
      "department": "Operations",
      "jobTitle": "Analyst",
      "dateOnboarded": "2026-10-05T00:00:00",
      "requestNotes": "Needs a laptop",
      "customFields": { "needs_vpn": true, "cost_centre": "OPS" }
    }
  }
]
```

`staff` also contains `mobilePhone`, address fields, `orgCompany`, `managerName`, `accountAction`,
`requestedAt` and `approvedAt`. Offboarding items include an `offboarding` object with
`outOfOfficeMessage`, `emailForwardTo` and `mailboxGrantEmails` when they were captured. Custom
fields are keyed by their internal name.

## Resume a workflow

```text
POST /api/staff/workflow-webhooks/{webhookId}
Content-Type: application/json
```

| JSON field | Type | Notes |
| --- | --- | --- |
| `postKey` | string | Required. The step's POST key. |
| `staffId` | integer | Recommended. Selects which paused workflow to resume; without it the most recently paused one is used. |
| `source` | string | Optional label recorded in the audit log. |
| `values` | object | Optional. Each entry becomes a workflow variable usable in later steps as `${vars.<name>}`. |
| `secretValues` | object of strings | Optional. Like `values`, but encrypted at rest and redacted from workflow logs. |
| `outcome` | `success` or `failed` | Defaults to `success`. `failed` stops the workflow, marks it failed and raises a helpdesk ticket. |
| `error` | string | Optional failure reason shown on the workflow and ticket when `outcome` is `failed`. |
| `payload` | object | Optional free-form data stored with the checkpoint for audit only. |

Value names use 1-64 letters, numbers, `_`, `-` or `.`. Names reserved by the workflow (`staff.*`,
`custom_fields.*`, `system.*`, `now.*`, `offboarding.*`, `company_id`, `staff_id` and similar) are
rejected. A value whose name contains `password`, `secret`, `token` or `key` is always treated as a
secret, even when sent in `values`. Up to 50 values (32 KB) may be sent.

```json
{
  "postKey": "<post key>",
  "staffId": 1001,
  "source": "ad-onboarding-script",
  "values": { "ad_username": "jane.starter", "ad_status": "pass" },
  "secretValues": { "ad_password": "Generated!Pass123" }
}
```

The workflow resumes immediately; the response (`202`) reports the resulting state, for example
`completed`, or `waiting_external` if it paused again at a later step. Later steps can then use
`${vars.ad_username}` or send `${vars.ad_password}` in an email or credential share step.

## PowerShell example

[`docs/examples/staff-workflow-webhook.ps1`](../../examples/staff-workflow-webhook.ps1) lists paused
workflows, creates each user in Active Directory, and resumes the workflow with the username and
generated password, or reports a failed outcome.

```powershell
.\staff-workflow-webhook.ps1 `
    -WebhookUrl "https://portal.example.com/api/staff/workflow-webhooks/<id>" `
    -PostKey "<post key>"
```
