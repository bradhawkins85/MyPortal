# OpnForm Integration Guide

MyPortal expects an OpnForm instance to run on the same host. The supplied
nginx snippet can reverse-proxy `/myforms/` to the OpnForm application so that the
builder opens within the same origin, while published forms displayed to end
users now load directly in an iframe from the OpnForm host. This document covers
provisioning OpnForm, wiring nginx, and exposing the builder link inside
MyPortal.

## 1. Provision OpnForm

The upstream OpnForm project publishes Docker images that bundle the API,
frontend, queue worker, and scheduler. The following example uses the official
`docker-compose.yml` from the project to keep the deployment self-contained on a
single server.

1. Clone the OpnForm repository:

   ```bash
   git clone https://github.com/JhumanJ/OpnForm.git /opt/opnform
   cd /opt/opnform
   ```

2. Copy the sample environment file and configure secrets. Ensure that generated
   keys are strong and unique.

   ```bash
   cp .env.example .env
   nano .env
   ```

   Recommended changes:

   - `APP_URL=https://portal.example.com/myforms` (match your public hostname)
   - `SESSION_DOMAIN=portal.example.com`
   - `APP_KEY=` (generate with `php artisan key:generate --show`)
   - Configure the database section to use a local MySQL/PostgreSQL instance or
     one of the bundled Docker database services.

3. Start OpnForm via Docker Compose. The default compose file publishes the API
   on `127.0.0.1:8080` and the queue workers internally.

   ```bash
   docker compose pull
   docker compose up -d
   ```

4. Run the database migrations and create an admin account:

   ```bash
   docker compose exec api php artisan migrate --force
   docker compose exec api php artisan opnform:create-admin
   ```

   Record the generated password securely and change it after first login.

## 2. Configure nginx

MyPortal ships with an nginx snippet (`deploy/nginx/opnform.conf`) that proxies
MyPortal and OpnForm from the same virtual host. Review the file, adjust the
`server_name`, and include it from your main nginx configuration. Reload nginx
after the file is in place:

```bash
sudo cp deploy/nginx/opnform.conf /etc/nginx/sites-available/myportal.conf
sudo ln -s /etc/nginx/sites-available/myportal.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Key security considerations:

- Only expose ports 80/443; the OpnForm and MyPortal processes should remain
  bound to `127.0.0.1`.
- Terminate TLS and enable HTTP/2 on the nginx server block (add `listen 443
  ssl http2;`).
- Issue certificates with an automated client such as Certbot and renew them on
  a schedule.
- Restrict nginx access to the `/myforms/` builder route by IP allow-lists or SSO
  if the forms should not be publicly browsable.

## 3. Tell MyPortal where OpnForm lives

MyPortal automatically links to `/myforms/`. When the reverse proxy needs to point
somewhere else (for example, a sub-domain), set the `OPNFORM_BASE_URL` variable
in `.env`. Embedded forms use this value to build the iframe `src`, so ensure the
URL allows cross-origin framing (e.g. send the proper `X-Frame-Options` /
`Content-Security-Policy` headers from OpnForm):

```env
OPNFORM_BASE_URL=https://forms.example.com/
```

The middleware normalises this URL so that templates, notifications, and future
integrations always generate consistent links.

## 4. Verify the integration

1. Sign in as the super admin and visit **Admin → Forms**.
2. Click **Open OpnForm**; the builder should open in a new tab.
3. Create or update a form in OpnForm and publish it.
4. From the OpnForm share dialog, copy the public form URL. MyPortal validates
   that the URL points to the configured OpnForm host.
5. Return to MyPortal and, under **Admin → Forms**, paste the URL into the
   **Form URL** field alongside the form name and description. Submit the form
   to save it.
6. Refresh the page—the form will now appear in the list for assignment to
   companies and users. When selected from the portal it loads directly from
   OpnForm in an iframe, so the upstream host must permit embedding from your
   MyPortal origin.

Troubleshooting tips:

- If the builder fails to load, review the nginx logs (`/var/log/nginx/error.log`)
  for proxy or permission issues.
- Ensure the OpnForm containers are healthy (`docker compose ps`). Restart the
  stack with `docker compose restart` if necessary.
- Confirm that both MyPortal and OpnForm share the same session cookie domain if
  you intend to implement SSO or embed forms with authenticated submissions.
- If forms refuse to render inside the iframe, double-check the OpnForm
  deployment does not send `X-Frame-Options: DENY` or a restrictive
  `Content-Security-Policy` that blocks framing by MyPortal.
