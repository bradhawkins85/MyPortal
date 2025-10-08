# MyPortal

MyPortal is now a Python-first customer portal built with FastAPI, async MySQL access, and Jinja-powered views. The application retains parity with the prior TypeScript experience while embracing a modern Python architecture that is easier to extend, test, and deploy.

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
- Basic shop with product SKUs and admin management API
- VIP pricing for companies with special product rates
- Shop admin interface to archive products and view archived items
- Order details include product image, SKU and description
- Port catalogue with searchable metadata, secure document uploads, and lifecycle tracking
- Pricing workflow approvals with notification feed and audit-friendly status changes
- Automated CSRF protection on authenticated state-changing requests
- Super admin access to the OpnForm builder for creating and editing forms

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
4. Copy `.env.example` to `.env` and update the MySQL credentials. Define strong values for `SESSION_SECRET` and `TOTP_ENCRYPTION_KEY`. Optional settings such as Redis, SMTP, and Azure Graph credentials mirror the legacy environment variables.
5. Start the development server:
   ```bash
   uvicorn app.main:app --reload
   ```
   On startup the application automatically applies any pending SQL migrations and ensures the database exists.
6. Access `http://localhost:8000` for the responsive portal UI or `http://localhost:8000/docs` for the interactive Swagger UI covering every API endpoint.
7. The first visit will redirect the login flow to the registration page if no users exist, ensuring the first account becomes the super administrator.

## Authentication API

All authentication routes are documented in the interactive Swagger UI and summarised below:

- `POST /auth/register` – Creates the first super administrator when no users exist and issues a session cookie.
- `POST /auth/login` – Authenticates credentials (and optional TOTP code) to establish a session and CSRF token.
- `POST /auth/logout` – Revokes the active session and clears authentication cookies.
- `GET /auth/session` – Returns the current session metadata and user profile.
- `POST /auth/password/forgot` – Generates a time-bound password reset token and triggers the outbound notification pipeline.
- `POST /auth/password/reset` – Validates the token and updates the user password with bcrypt hashing.
- `GET /auth/totp` – Lists active TOTP authenticators for the current user.
- `POST /auth/totp/setup` – Generates a pending TOTP secret and provisioning URI for enrolment.
- `POST /auth/totp/verify` – Confirms the authenticator code and persists it for future logins.
- `DELETE /auth/totp/{id}` – Removes an existing authenticator.

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

The legacy `update.sh` script has been preserved for historical reference, however the recommended approach is to pull changes, update the Python dependencies, and restart the ASGI server:

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
