# Uptime Kuma Integration

The Uptime Kuma module allows MyPortal to ingest monitoring alerts directly from
Uptime Kuma webhooks. Once enabled, incoming HTTP POST requests are persisted,
searchable through the admin API, and available for automations.

## Configuration Steps

1. Navigate to **Admin → Integration modules → Uptime Kuma**.
2. Toggle **Module enabled** and save.
3. Generate a strong shared secret in the **Shared secret** field. The secret is
   stored as a SHA-256 hash; leave the field blank to retain the existing hash.
4. Copy the **Webhook URL** displayed on the module card. This URL resolves to
   `/api/integration-modules/uptimekuma/alerts` on your instance.
5. In Uptime Kuma, create a new webhook notification:
   - Set the HTTP method to `POST`.
   - Paste the webhook URL.
   - Provide the shared secret using either the `Authorization: Bearer <secret>`
     header or by appending `?token=<secret>` to the webhook URL.
   - Choose `JSON` as the payload format.
6. Trigger a test notification from Uptime Kuma to verify that the request is
   accepted (HTTP 202) and listed in the MyPortal alert log.

## Payload Handling

The ingestion endpoint accepts both the native JSON webhook schema emitted by
Uptime Kuma and the condensed Apprise notification format.

### Native Uptime Kuma webhook

Configure the Uptime Kuma notification type as **Webhook** with JSON body.
Uptime Kuma populates fields such as `status`, `monitorName`, `monitorID`,
`msg`, `duration`, `ping`, and `time`.

### Apprise notification

When Uptime Kuma is configured to send alerts via the **Apprise** notification
type pointing at this endpoint, Apprise delivers a simplified payload:

```json
{
  "version": "1.0",
  "title": "[UP] My Service",
  "message": "My Service is back online",
  "type": "success"
}
```

The `type` field (`success`, `failure`, `warning`, `info`) is used as the
alert status when no explicit `status` value is present, and the monitor name
is extracted from the `title` prefix (e.g. `[UP] My Service` → `My Service`).

### Recorded fields

The following fields are parsed and stored for search and filtering:

- `monitor_id`, `monitor_name`, `monitor_url`
- `status` and `previousStatus`
- `alert_type`, `reason`, and `msg`/`message`
- Duration and ping metrics
- Event identifiers (`uuid`, `incidentId`, or the provided ID field)
- Remote address and user agent of the caller

Timestamps are normalised to UTC prior to storage.

## API Endpoints

All routes are documented in the Swagger UI once authenticated as a super
administrator:

- `POST /api/integration-modules/uptimekuma/alerts` – public ingestion endpoint
  secured with the shared secret.
- `GET /api/integration-modules/uptimekuma/alerts` – list stored alerts with
  filtering and sorting parameters.
- `GET /api/integration-modules/uptimekuma/alerts/{alert_id}` – retrieve a
  single alert payload.

Super administrators can combine the alert API with automations to create
notifications or tickets from outage events.
