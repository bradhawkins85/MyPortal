# Xero Integration

The Xero module synchronises invoices and customer metadata between MyPortal and
Xero. Before MyPortal can refresh OAuth tokens or receive webhook
notifications, register an application inside the Xero developer portal and
record the generated credentials inside **Admin → Integration modules → Xero**.

## Callback URL

Xero requires a callback URL (also called a redirect URI) for OAuth flows and
webhook notifications. MyPortal exposes a dedicated endpoint at:

```
https://<your-domain>/api/integration-modules/xero/callback
```

This URL is displayed inside the Xero module configuration panel so that it can
be copied without leaving the admin interface. Paste the fully-qualified URL
into the **Redirect URI** field within your Xero application settings.

The endpoint accepts POST requests and responds with HTTP 202 to acknowledge
callbacks from Xero. A lightweight GET handler is also available for diagnostic
probes and returns HTTP 200 when the integration module is enabled.

## Ticket billing controls

Super administrators can fine tune which tickets are synchronised by supplying
comma-separated technical statuses inside the **Billable ticket statuses**
field. Only tickets matching one of the configured statuses are considered for
invoicing during the scheduled synchronisation runs.

When the scheduler runs it reuses any invoice that MyPortal has already created
for the same company on the same UTC date, appending new billable tickets as
extra line items instead of creating duplicate invoices. This keeps batched
ticket runs grouped together without manual reconciliation.

Each line item description is generated from a configurable template. The
default mirrors the previous behaviour (`Ticket {ticket_id}: {ticket_subject}`),
and additional placeholders are available for labour-specific information:

- `{ticket_id}`, `{ticket_subject}`, `{ticket_status}`
- `{labour_name}`, `{labour_code}`, `{labour_minutes}`, `{labour_hours}`
- `{labour_suffix}` – resolves to ` · {labour_name}` when a labour type exists

For example, setting the template to `Ticket #{ticket_id} - {ticket_subject} -
{labour_name}` produces descriptions such as `Ticket #123 - Fix Computer -
Remote`.

## Security

Callbacks are only accepted when the Xero module is enabled. Disable the module
from the admin interface to immediately stop accepting inbound requests. Any
headers prefixed with `X-Xero-` are logged for troubleshooting without
persisting the full payload, ensuring sensitive information remains protected.
