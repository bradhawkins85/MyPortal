# Tactical RMM ticket webhook

Tactical RMM alerts can create MyPortal tickets with:

```text
POST /api/tickets/tacticalrmm
X-API-Key: <MyPortal API key>
Content-Type: application/json
```

Unlike the general ticket endpoint, `company_id` on this endpoint is the
**Tactical RMM client ID**. MyPortal resolves it through the company's Tactical
RMM mapping. `agent_id` is optional; when supplied, it is treated as a Tactical
RMM agent ID and the corresponding imported MyPortal asset is linked to the
ticket.

Example alert body:

```json
{
  "subject": "{{alert.alert_type}} on {{alert.agent}}",
  "description": "{{alert.message}}",
  "status": "new",
  "priority": "normal",
  "category": "{{alert.alert_type}}",
  "company_id": "{{alert.client.id}}",
  "agent_id": "{{alert.agent.id}}",
  "alert_id": "{{alert.id}}"
}
```

`alert_id` is required and must contain Tactical RMM's `Alert` primary key.
MyPortal stores it as the namespaced ticket external reference
`tacticalrmm:alert:<alert_id>`. This makes delivery retries idempotent: posting
the same alert again returns its existing ticket instead of creating a
duplicate. The endpoint does not accept a caller-selected external reference.

Configure a second webhook for Tactical RMM's resolved alert notification:

```text
POST /api/tickets/tacticalrmm/resolved
X-API-Key: <MyPortal API key>
Content-Type: application/json
```

Resolved alert body:

```json
{
  "alert_id": "{{alert.id}}"
}
```

The resolved webhook locates the ticket by its generated external reference
and changes it to MyPortal's `resolved` status. Repeated resolved notifications
are safe and return the already-resolved ticket. A missing alert association
returns `404` without changing any ticket.

`requester_id` and `assigned_user_id` are optional and should normally be
omitted. If an existing Tactical RMM template sends them, the endpoint accepts
but ignores them because Tactical RMM user IDs are not MyPortal user IDs.

The request returns `404` without creating a ticket when the Tactical RMM
client mapping (or optional agent mapping) cannot be found. Configure company
mappings and import Tactical RMM assets before using the corresponding IDs.
