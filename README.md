# MyPortal

MyPortal is now a Python-first customer portal built with FastAPI, async MySQL access, and Jinja-powered views. The application retains parity with the previous portal experience while embracing a modern Python architecture that is easier to extend, test, and deploy.

There are no default login credentials; the first visit will prompt you to register the initial super administrator. If no user records exist the login flow transparently redirects to the registration screen.

## Features

- Session-based authentication with secure cookies and sliding expiration
- Built-in rate limiting, CSRF protection, and password reset flows
- Optional TOTP multi-factor authentication with QR-code provisioning
- Business information summary tab to confirm the logged-in company
- Licenses tab showing license name, SKU, count, allocated staff, expiry date and contract term
- Centralised company membership management with reusable roles and real-time audit logging
- Super admin UI for reviewing membership changes and role edits with filtering and sorting controls
- Sidebar company switcher so administrators can pivot between assigned companies without re-authenticating
- First-time visit redirects to a registration page when no users exist
- Self-service registration for additional users grants requester-scoped ticket access with public reply visibility only
- Basic shop with product SKUs and admin management API
- VIP pricing for companies with special product rates
- Shop admin interface to archive products and view archived items
- Order details include product image, SKU and description
- Customisable shipping status notifications with per-channel delivery preferences
- Port catalogue with searchable metadata, secure document uploads, and lifecycle tracking
- Pricing workflow approvals with notification feed and audit-friendly status changes
- Super administrators can publish portal alerts with the `/api/notifications` API for targeted or global announcements
- Knowledge base with permission-scoped articles, granular visibility controls, Ollama-backed natural language search, and a super admin composer for publishing and editing entries
- Automated CSRF protection on authenticated state-changing requests
- Super admin access to the OpnForm builder for creating and editing forms
- Automation dashboard with persistent scheduler management, webhook retry monitoring, and admin controls
- Ticketing workspace with replies, watchers, and module-aligned categorisation surfaced through API and admin UI
- Automation builder covering scheduled and event-driven workflows with Ollama, SMTP, TacticalRMM, and ntfy integrations
- Integration module catalogue to manage external credentials, run diagnostics, and ensure webhook retries remain observable
- ChatGPT MCP module providing secure ticket triage tools and automations to OpenAI ChatGPT via the Model Context Protocol
- Syncro ticket importer with super admin UI controls, rate limiting, and REST API access for bulk migrations

## Syncro Ticket Importer

Super administrators can synchronise Syncro tickets into MyPortal directly from **Admin → Tickets**. The import console offers
three modes:

- **Single ticket** – supply a Syncro ticket ID for one-off imports or re-syncs.
- **Range import** – provide `startId` and `endId` values to sweep a consecutive block of tickets.
- **Import all tickets** – iterate through the entire Syncro queue using the 25-item pagination window.

Each request observes the Syncro 180 requests-per-minute ceiling and reports how many tickets were created, updated, or skipped
after upserting into MyPortal.

The same workflow is exposed through the Swagger-documented endpoint `POST /api/tickets/import/syncro`. The request body accepts
camelCase or snake_case keys:

```json
{
  "mode": "range",
  "startId": 1500,
  "endId": 1525
}
```

Use `"mode": "single"` with `ticketId` for targeted imports or `"mode": "all"` without additional fields to crawl every page.
The response echoes the mode alongside `fetched`, `created`, `updated`, and `skipped` counters so integrations can audit the
result.

## Port Catalogue & Pricing Workflows

The portal now exposes a dedicated port catalogue that allows super administrators
to maintain structured metadata (name, country, timezone, region, geographic
coordinates) for each port. Authenticated users can search and filter the
catalogue via the `/ports` API while super administrators can create, update,
and archive entries. Each port supports secure document uploads—files are
sanitised, size-limited to 15&nbsp;MB, stored under `app/static/uploads`, and
served through the `/static/uploads/...` namespace. Metadata about the
uploader, upload time, and file characteristics is recorded alongside the
database entry.

Pricing operations are handled through workflow-oriented endpoints that allow
teams to draft, submit, approve, or reject rate cards for each port. Every
transition captures the actor, timestamps (stored in UTC), currency, and
effective dates so audit requirements are met. Approval and rejection actions
automatically create notifications that appear in the `/notifications` feed,
ensuring requesters are alerted as statuses change. The interactive Swagger UI
lists the full set of CRUD routes and supported query parameters for filtering
and sorting.

## Notifications API

Authenticated users can review their notification feed at `/notifications` or via
`GET /api/notifications`, filtering by event type, read status, search terms,
and time ranges. Super administrators may now create notifications directly via
`POST /api/notifications`, supplying an `event_type`, human readable `message`,
optional `user_id` recipient, and structured metadata payload. When `user_id`
is omitted the notification is treated as a broadcast for every user. Newly
created notifications are surfaced immediately in the UI and through the
Swagger UI to support automated integrations and operational tooling.

Each user can tailor how those events are delivered from `/notifications/settings`
or programmatically through `GET`/`PUT /api/notifications/preferences`. The
preferences API returns the merged catalogue of known event types and delivery
channels (in-app feed, email, SMS) while updates persist the full set of
choices in a single request. Default events now include shipping status updates
(`shop.shipping_status_updated`) so customers can follow fulfilment progress
alongside billing, port, and webhook alerts.

Supporting endpoints expose aggregated counts and merged event type catalogues
for richer clients:

- `GET /api/notifications/summary` – Returns the total notifications and unread
  counts matching the supplied filters alongside the global unread tally.
- `GET /api/notifications/event-types` – Provides the distinct notification
  event types available to the authenticated user by combining defaults,
  preferences, and recorded history.

## ChatGPT MCP Module

The ChatGPT MCP integration exposes a dedicated `/api/mcp/chatgpt` endpoint so
ChatGPT can triage tickets, append replies, and (optionally) update ticket
metadata using the [Model Context Protocol](https://modelcontextprotocol.io/).

1. Visit **Admin → Integration modules → ChatGPT MCP** and generate a strong
   shared secret. The secret is stored as a SHA-256 hash and must also be
   configured inside ChatGPT when registering the MCP server.
2. Select which tools ChatGPT may call. Available tools include `listTickets`,
   `getTicket`, `createTicketReply`, and `updateTicket`. Disable ticket updates
   to enforce read-only access.
3. Set a maximum ticket count (default 50) and, if ChatGPT should post
   replies, supply a system user ID to attribute those updates to.
4. From ChatGPT, configure an MCP server pointing at
   `https://<your-domain>/api/mcp/chatgpt` and present the shared secret via a
   `Bearer` token. The server responds to `initialize`, `listTools`, and
   `callTool` JSON-RPC requests.

Refer to `docs/chatgpt-mcp.md` for the complete JSON payload examples,
per-tool argument schemas, and troubleshooting guidance.

## Template Variables for External Apps

MyPortal exposes a curated set of template variables that can be embedded in
form URLs or other external application links. When a page is rendered these
placeholders are substituted with details from the logged-in user and their
currently selected company. Each variable is available in two forms:

- `{{variable}}` – the raw value.
- `{{variable}}UrlEncoded` – the same value pre-encoded with
  `encodeURIComponent` so it can be safely appended to query strings.

| Placeholder | Description |
| --- | --- |
| `{{user.email}}` | Email address for the logged-in user. |
| `{{user.firstName}}` | User's first name. |
| `{{user.lastName}}` | User's last name. |
| `{{user.fullName}}` | Combination of first and last name with whitespace trimmed. |
| `{{company.id}}` | Numeric identifier of the active company. |
| `{{company.name}}` | Name of the active company. |
| `{{company.syncroId}}` | Syncro customer ID when available for the company. |
| `{{portal.baseUrl}}` | Base URL of the MyPortal instance. |
| `{{portal.loginUrl}}` | Direct link to the MyPortal login page. |

For example, a form URL such as
`https://forms.example.com/start?email={{user.emailUrlEncoded}}&company={{company.nameUrlEncoded}}`
will resolve to the current user's email and company at runtime. Missing values
gracefully fall back to an empty string.

## Setup

1. Ensure Python 3.10+ is available.
2. Create a project-local virtual environment so `pip install -e .` does not conflict with externally managed Python installations:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use: .\.venv\\Scripts\\activate
   ```
   The helper script below automates the process and installs dependencies in one step. Pass `--recreate` to rebuild the environment from scratch.
   ```bash
   python scripts/bootstrap_venv.py
   ```
3. If you did not run the bootstrap script, upgrade pip inside the virtual environment and install dependencies:
   ```bash
   python -m pip install --upgrade pip
   pip install -e .
   ```
4. Copy `.env.example` to `.env` and update the MySQL credentials. Define strong values for `SESSION_SECRET` and `TOTP_ENCRYPTION_KEY`. Optional settings such as Redis, SMTP, and Azure Graph credentials mirror the legacy environment variables. Configure `SMS_ENDPOINT` and `SMS_AUTH` when enabling outbound SMS notifications so the portal can relay messages to your gateway securely.
5. Start the development server:
   ```bash
   uvicorn app.main:app --reload
   ```
   On startup the application automatically applies any pending SQL migrations and ensures the database exists.
6. Access `http://localhost:8000` for the responsive portal UI. After signing in, visit `http://localhost:8000/docs` for the interactive Swagger UI covering every API endpoint.
7. The first visit will redirect the login flow to the registration page if no users exist, ensuring the first account becomes the super administrator.

## Fail2ban Support

Set the optional `FAIL2BAN_LOG_PATH` variable in your `.env` file to mirror authentication events to a dedicated log file. When configured, MyPortal records structured messages such as `AUTH LOGIN FAIL email=user@example.com ip=203.0.113.10 reason=invalid_credentials`, which align with the sample filter at `deploy/fail2ban/myportal-auth.conf`. Pair the filter with the example jail configuration in `deploy/fail2ban/myportal-auth.local`, updating the `logpath` to match your environment. After copying both files into `/etc/fail2ban/{filter.d,jail.d}/`, restart the Fail2ban service so repeated login failures from the same IP are automatically banned.

## Authentication API

All authentication routes are documented in the interactive Swagger UI and summarised below:

- `POST /auth/register` – Creates the first super administrator when no users exist and issues a session cookie.
- `POST /auth/login` – Authenticates credentials (and optional TOTP code) to establish a session and CSRF token.
- `POST /auth/logout` – Revokes the active session and clears authentication cookies.
- `GET /auth/session` – Returns the current session metadata and user profile.
- `POST /auth/password/forgot` – Generates a time-bound password reset token and triggers the outbound notification pipeline.
- `POST /auth/password/reset` – Validates the token and updates the user password with bcrypt hashing.
- `POST /auth/password/change` – Allows an authenticated user to rotate their password after validating the current credential.
- `GET /auth/totp` – Lists active TOTP authenticators for the current user.
- `POST /auth/totp/setup` – Generates a pending TOTP secret and provisioning URI for enrolment.
- `POST /auth/totp/verify` – Confirms the authenticator code and persists it for future logins.
- `DELETE /auth/totp/{id}` – Removes an existing authenticator.

## API Key Management

Super administrators can now mint and revoke API credentials directly from the Swagger UI or via the endpoints below. Each key
is generated as a 64-character token, stored using an HMAC-SHA256 digest peppered with the global secret, and summarised with a
preview prefix so the plaintext is never written to the database.【F:app/security/api_keys.py†L1-L38】【F:app/repositories/api_keys.py†L1-L190】

- `GET /api-keys` – Lists active and (optionally) expired keys with usage counts, last-seen timestamps, and per-IP access
  breakdowns. Sorting, free-text search, and inclusion of expired keys are supported via query parameters.【F:app/api/routes/api_keys.py†L24-L62】
- `POST /api-keys` – Creates a new key, returning the plaintext once alongside its metadata; the operation is captured in the
  audit log with the requesting administrator and source IP.【F:app/api/routes/api_keys.py†L64-L92】
- `GET /api-keys/{id}` – Retrieves a single key record with aggregated usage telemetry for investigative workflows.【F:app/api/routes/api_keys.py†L95-L105】
- `DELETE /api-keys/{id}` – Revokes a key, removes its usage counters, and logs the previous metadata for auditing.
  【F:app/api/routes/api_keys.py†L108-L123】【F:app/repositories/api_keys.py†L132-L190】

## Company Context Switching

- `POST /switch-company` – Updates the active company for the authenticated session. The endpoint accepts either
  form-encoded or JSON payloads with a `companyId` field and honours an optional `returnUrl` parameter. Clients may
  also supply these parameters via the query string when making server-side redirects. A valid CSRF token is required
  for authenticated browsers; send it as the `_csrf` form field or `X-CSRF-Token` header.

## Office 365 Sync

To enable Microsoft 365 license synchronization, register an application in
Azure Active Directory and grant it the required Graph permissions.

1. Sign in to the Azure portal and open **Azure Active Directory** →
   **App registrations** → **New registration**.
2. Choose a name for the application and select the supported account types
   for your tenant.
3. Under **Redirect URI**, select **Web** and enter
   `https://<your-domain>/m365/callback`.
4. After creation, note the **Application (client) ID** and **Directory (tenant)
   ID**.
5. Create a client secret in **Certificates & secrets** and record the value; it
   is shown only once.
6. In **API permissions**, add Microsoft Graph **Application permissions**
   `Directory.Read.All` and `User.Read.All`, then grant admin consent.
7. Open the Office 365 admin page in MyPortal and enter the tenant ID, client
   ID and client secret. The credentials can be edited or removed at any time.
8. From the Office 365 page for a company, click **Authorize** to grant the
   application access. The portal requests the `offline_access` scope so that a
   refresh token is stored for background sync jobs.

## File Storage

Static assets located under `app/static` are served directly. Port documents and
other uploads are written to `app/static/uploads`, grouped by port identifier,
with sanitised filenames and a 15&nbsp;MB size cap. The upload API requires an
authenticated session and records metadata about the uploader, original file
name, content type, and size so administrators can audit stored files. Because
files live under the static directory they are subject to existing theme and
branding controls; access controls should still be enforced via the API layer
when linking to documents.

## CSRF Protection

Authenticated POST, PUT, PATCH, and DELETE routes require a CSRF token. After
login the API sets a `myportal_session_csrf` cookie containing a random token
that must be echoed back via the `X-CSRF-Token` header (or `_csrf` form field)
on mutating requests. The cookie is readable by client-side JavaScript so that
single-page enhancements can propagate the header automatically.

## Deployment

Run the service with Uvicorn or Gunicorn in production. A representative command using Uvicorn is shown below:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

The application emits structured logs via Loguru and applies database migrations on boot, ensuring zero-touch deployments. Supervisor, systemd, Docker, or any process manager can be used based on your infrastructure standards.

For a hardened systemd configuration that runs MyPortal as a managed Linux service, see [docs/systemd-service.md](docs/systemd-service.md). The guide covers creating a dedicated service account, isolating environment variables, configuring auto-restarts, and verifying the unit status.

## Updating from GitHub

Use the Python-focused automation scripts to stay current: `scripts/upgrade.sh` pulls the latest code and `scripts/restart.sh` reinstalls dependencies before restarting the ASGI service. If you prefer to run the steps manually, execute the following commands:

```bash
git pull origin main
pip install -e .
systemctl restart myportal.service
```

## OpnForm Integration

MyPortal expects an OpnForm instance on the same server. nginx can still proxy
`/myforms/` to that service so that super admins can launch the builder directly
from the Forms admin area. See [docs/opnform.md](docs/opnform.md) for deployment
and security guidance, including the supplied nginx configuration snippet in
[`deploy/nginx/opnform.conf`](deploy/nginx/opnform.conf).

When registering a form inside MyPortal, paste the published OpnForm form URL
into the Forms admin. The server validates that the URL targets the expected
OpnForm host (when configured) and stores it. Forms assigned to users now load
directly in an iframe without being proxied through MyPortal, and template
variables remain available so query string parameters can be personalised for
the current user and company.
