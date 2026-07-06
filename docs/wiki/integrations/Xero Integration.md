# Xero Integration

The Xero module synchronises invoices and customer metadata between MyPortal and
Xero. Before MyPortal can refresh OAuth tokens or receive webhook
notifications, register an application inside the Xero developer portal and
record the generated credentials inside **Admin → Integration modules → Xero**.

## Callback and webhook URLs

Xero uses separate URLs for OAuth redirects and signed webhook notifications.
Paste the OAuth callback URL into the **Redirect URI** field within your Xero
application settings:

```
https://<your-domain>/api/integration-modules/xero/callback
```

Paste the webhook URL into the Xero webhook delivery URL field:

```
https://<your-domain>/api/integration-modules/xero/webhook
```

The webhook endpoint validates the raw request body against the
`x-xero-signature` header using the configured Xero webhook signing key. During
Xero's Intent to Receive test, Xero sends one correctly signed payload and three
incorrectly signed payloads; MyPortal returns an empty HTTP 200 response for the
correctly signed payload and an empty HTTP 401 response for each incorrectly
signed payload, which is the status-code contract Xero requires before enabling
webhook delivery. The empty-events Intent to Receive payload is acknowledged
without invoice processing.

The legacy callback endpoint accepts POST requests and responds with HTTP 202 to
acknowledge callbacks from Xero. A lightweight GET handler is also available for
diagnostic probes and returns HTTP 200 when the integration module is enabled.

## Invoice synchronisation

The scheduled **Sync to Xero** commands now upload invoices that already exist
inside MyPortal. After Xero accepts an invoice, MyPortal stores the returned
Xero invoice identifier, marks the invoice as synchronised, and renames the
local invoice number to match the Xero invoice number. Running the sync again
only uploads invoices that do not already have a stored Xero invoice ID.

When invoice line items include `ItemCode` values that do not exist in Xero,
MyPortal can automatically create the missing Xero products and retry the
invoice submission. This is controlled by the **Automatically create missing
Xero products from invoice item codes** setting in the Xero module (enabled by
default).

### Labour Type Rates

MyPortal automatically fetches billing rates for labour types from Xero, enabling different rates for different types of work:

- **Remote Support** might be billed at $95/hour
- **On-site Support** might be billed at $150/hour  
- **After Hours** might be billed at $200/hour

To use this feature:

1. Create labour types in MyPortal (**Admin > Tickets > Labour Types**) with unique codes
2. Create matching items in Xero with the same codes
3. Set the Unit Price for each item in Xero

When syncing tickets, MyPortal will:
- Fetch the Unit Price from Xero for each labour type code
- Use that price instead of the default hourly rate
- Fall back to the default hourly rate if no matching Xero item is found

This allows you to maintain all pricing in Xero, and changes to rates in Xero automatically apply to future invoices without needing to update MyPortal configuration.

### Invoice Grouping

When the scheduler runs it reuses any invoice that MyPortal has already created
for the same company on the same UTC date, appending new billable tickets as
extra line items instead of creating duplicate invoices. This keeps batched
ticket runs grouped together without manual reconciliation.

Each line item description is generated from a configurable template. The
default includes the labour duration in hours and minutes
(`Ticket {ticket_id}: {ticket_subject} {labour_suffix} ({labour_duration})`),
and additional placeholders are available for labour-specific information:

- `{ticket_id}`, `{ticket_subject}`, `{ticket_status}`
- `{labour_name}`, `{labour_code}`, `{labour_minutes}`, `{labour_hours}`, `{labour_duration}`
- `{labour_suffix}` – resolves to the labour type name when present; add any separators or spacing in the template

For example, setting the template to `Ticket #{ticket_id} - {ticket_subject} -
{labour_name} ({labour_duration})` produces descriptions such as
`Ticket #123 - Fix Computer - Remote (30 Mins)`.

## Quote sync

Quotes can now be synchronised from MyPortal to Xero as draft invoices via:

- `POST /api/quotes/{quote_number}/sync-to-xero?companyId=<id>`

The endpoint uses quote products as Xero line items and applies the same
missing-product auto-create behavior.

## Security

Callbacks are only accepted when the Xero module is enabled. Disable the module
from the admin interface to immediately stop accepting inbound requests. Any
headers prefixed with `X-Xero-` are logged for troubleshooting without
persisting the full payload, ensuring sensitive information remains protected.
