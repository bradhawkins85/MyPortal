# MyPortal

MyPortal is a simple customer portal built with Node.js, TypeScript and MySQL. It allows users from a company to log in and view company information and allocated software licenses.

There are no default login credentials; the first visit will prompt you to register.

## Features

- Session-based authentication for multiple users
- Business information summary tab to confirm the logged-in company
- Licenses tab showing license name, SKU, count, allocated staff, expiry date and contract term
- First-time visit redirects to a registration page when no users exist
- Basic shop with product SKUs and admin management API
- VIP pricing for companies with special product rates
- Shop admin interface to archive products and view archived items
- Order details include product image, SKU and description
- CSRF protection on authenticated state-changing requests

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```
2. Copy `.env.example` to `.env` and update the MySQL credentials. Set high-entropy
   values for `SESSION_SECRET` and `TOTP_ENCRYPTION_KEY` (e.g. via `openssl rand -hex 32`).
   Optionally set `CRON_TIMEZONE` to control the timezone for scheduled tasks (defaults to UTC).
3. On first run, the application will automatically apply the database schema and encrypt any existing TOTP secrets.
4. On first run, visiting `/login` will redirect to a registration page to create the initial user and company.
5. Run in development mode:
   ```bash
   npm run dev
   ```
6. Build and start the application:
   ```bash
   npm run build
   npm start
   ```
7. Run type checks:
   ```bash
   npm test
   ```

## File Storage

Static assets located under `src/public` are served directly and are intended
to be publicly accessible. User-uploaded files and other sensitive assets are
stored in the `private_uploads` directory outside of the public tree. These
files are only available through the `/uploads/:filename` route, which now
requires authentication. Generating a signed URL can be used to grant
temporary access if public sharing is needed.

## CSRF Protection

Authenticated POST, PUT and DELETE routes require a CSRF token. Tokens are
automatically embedded in forms rendered by the server. For custom forms or
JavaScript requests, read the token from the `csrf-token` meta tag and include
it as the `_csrf` form field or the `CSRF-Token` header.

## Running with PM2

PM2 can be used to manage the application in production. After building the
project, start the compiled server with PM2:

```bash
npm run build
pm2 start dist/server.js --name myportal
```

To view logs or restart the process later:

```bash
pm2 logs myportal
pm2 restart myportal
```

## Updating from GitHub

Run the included update script to fetch the latest changes and rebuild the project:

```bash
./update.sh
```

If the repository is private or otherwise requires authentication, set
`GITHUB_USERNAME` and `GITHUB_PASSWORD` before running the script (they may
also be supplied as the first two positional arguments). When using a
fine-grained personal access token as the password, grant the token access to
this repository with the **Contents: Read-only** permission.

Alternatively, to run the steps manually:

```bash
git pull origin main
npm install
npm run build
pm2 restart myportal
```
