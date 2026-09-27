# Setup and Installation

MyPortal is a Python-first customer portal built with FastAPI, async MySQL
access, and Jinja-powered views. Two scripts install it on a Debian/Ubuntu host
(bare metal, a VM or an LXC container) without any manual steps:

| Script | Use it for |
| --- | --- |
| `scripts/install_production.sh` | A production server. Serves the portal through nginx on port 80 using immutable blue/green releases. |
| `scripts/install_development.sh` | A developer checkout. Editable install, isolated database, optional `myportal-dev` service on port 8000. |

Both scripts are safe to re-run; existing secrets, database passwords and data
are preserved.

## Supported platforms

| Requirement | Supported |
| --- | --- |
| Operating system | Ubuntu 24.04 LTS or Debian 12 (or newer). Both must use `apt-get` and, for services, `systemd`. |
| Python | 3.10 or newer (the distribution's `python3`) |
| Database | **MariaDB 10.10 or newer.** The installers install the distribution's `mariadb-server` when `DB_HOST` is local. Oracle MySQL is not supported, because the schema migrations use MariaDB-only syntax. |
| Network access | The distribution's package mirrors, PyPI, and the Git remote (`origin`) of your checkout |

Ubuntu 22.04 ships MariaDB 10.6, which is too old. On 22.04, install MariaDB
10.11 LTS from [mariadb.org](https://mariadb.org/download/) first, or point
`DB_HOST` at a supported server.

The installers also install these packages:

- git, curl and util-linux
- the WeasyPrint/libmagic runtime libraries (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`, `libmagic1`)
- the `baresip` SIP client
- nginx and cron (production only)

PowerShell Core and the ExchangeOnlineManagement module are installed when
Microsoft's repositories are reachable. They are optional: without them only
the Exchange Online PowerShell fallback is unavailable.

## Production installation

Clone the repository as root into `/opt/myportal/control` and run the
production installer:

```bash
sudo git clone https://github.com/bradhawkins85/MyPortal /opt/myportal/control
sudo /opt/myportal/control/scripts/install_production.sh
```

The installer:

1. Installs the system packages listed above.
2. Creates the unprivileged `myportal` service account, plus `/var/log/myportal`
   and `/var/lib/myportal`.
3. Creates `/etc/myportal.env` from `.env.example`, owned by `root:myportal`
   with mode 0640. It generates strong values for `SESSION_SECRET`,
   `TOTP_ENCRYPTION_KEY`, `SMTP2GO_WEBHOOK_SECRET`, `MCP_TOKEN` and
   `DB_PASSWORD`. Existing values are never rotated. If the checkout
   contains a `.env` from an older installation, that file is copied instead.
4. Installs MariaDB (when `DB_HOST` is local), then creates the database and
   the application account.
5. Runs `scripts/upgrade.sh` for the first deployment, which:
   - exports `origin/main` into `/opt/myportal/releases/<sha>` and builds its
     virtualenv from `requirements.lock`
   - applies the database migrations
   - starts a `myportal@blue`/`myportal@green` systemd slot and checks that it
     is ready
   - installs the nginx site on port 80 (disabling nginx's stock default
     site, and dropping the IPv6 listener when the host has no IPv6)
   - retires the old single-checkout `myportal.service` if one exists
6. Installs `/etc/cron.d/myportal-update`, which applies updates requested from
   the admin UI.

When it finishes, open `http://<server>/` and register. **The first account
becomes the super administrator** and is asked to enrol two-factor
authentication.

The first deployment deploys `origin/main` of the checkout, not the files in
your working tree.

### Before exposing the portal to the internet

The bundled nginx site listens on plain HTTP. Put TLS in front of it (a reverse
proxy, a load balancer, or certbot's nginx plugin). Then, in
`/etc/myportal.env`, set:

- `PORTAL_URL` to the public `https://` address
- `ENVIRONMENT=production`, which enables Secure-only cookies, HSTS and
  production secret checks

Apply the change with:

```bash
sudo systemctl restart myportal@blue.service myportal@green.service
```

Do not set `ENVIRONMENT=production` while you still reach the portal over plain
HTTP: browsers will not send Secure cookies, so logins will fail.

### Updating a production server

```bash
sudo /opt/myportal/control/scripts/upgrade.sh
```

The upgrade prepares the new release, runs migrations and verifies the idle
slot before nginx switches to it. If anything fails, it rolls back to the
previous release. See [Zero Downtime Upgrades](https://github.com/bradhawkins85/MyPortal/blob/main/docs/wiki/getting-started/Zero%20Downtime%20Upgrades.md).

Super administrators can also trigger an update from the portal. The
application writes an update request to `/opt/myportal/shared/state`, and the
cron job applies it within a minute. This requires the control checkout to live
outside `/home` and `/root`, because the service units run with
`ProtectHome=true`.

### Migrating an installation made by the old installer

Older versions of `install_production.sh` ran a single `myportal.service` from
the checkout on port 8000. To move such a host to the current layout, update the
checkout and re-run the installer:

```bash
cd /path/to/checkout
sudo git pull
sudo scripts/install_production.sh
```

This copies the checkout's `.env` to `/etc/myportal.env` unchanged, keeping
secrets and the database password. It also copies existing uploads into
`/opt/myportal/shared` and deploys the blue/green release. Once the new release
is serving, it removes `myportal.service`.

### Useful locations

| Path | Purpose |
| --- | --- |
| `/etc/myportal.env` | Configuration and secrets |
| `/opt/myportal/control` | Control checkout that releases are exported from |
| `/opt/myportal/current` | The serving release |
| `/opt/myportal/shared` | Uploads, update state and other persistent data |
| `/var/log/myportal/` | Application log and the update cron log |

Check health with `curl http://localhost/readyz`, and view logs with
`journalctl -u 'myportal@*'`.

## Development installation

Clone the repository as your own user and run the development installer:

```bash
git clone https://github.com/bradhawkins85/MyPortal ~/MyPortal
cd ~/MyPortal
sudo scripts/install_development.sh   # or run it without sudo; see below
```

The development installer:

1. Installs the system packages (using `sudo` for `apt-get` when needed).
2. Creates `<checkout>/.env` from `.env.example`, generates the secrets, and
   uses its own database (`DB_NAME=myportal_dev`, `DB_USER=myportal_dev`).
   This keeps it isolated from a production installation that shares the
   same MariaDB server.
3. Installs MariaDB when needed and provisions that database.
4. Creates `<checkout>/.venv` and installs the pinned dependencies from
   `requirements.lock`, plus MyPortal in editable mode with the `dev`
   extras (pytest and friends).
5. Applies the database migrations.
6. When run with `sudo` on a systemd host, installs and starts
   `myportal-dev.service`. It runs as you, from your checkout, on port 8000
   (change it with `DEV_SERVER_PORT` in `.env`).

Everything in the checkout stays owned by you, even when you run the installer
with `sudo`.

Without `sudo` no service is created. Start the server yourself:

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

After pulling new code, apply new migrations with:

```bash
.venv/bin/python manage.py migrate --target-release development
```

Then open http://localhost:8000. The first registered account becomes the
super administrator. The API documentation is at http://localhost:8000/docs.

## Database migrations

Migrations live in `migrations/`. They are **not** applied when the application
starts. Production applies them in `scripts/upgrade.sh` before any release is
activated; development applies them with `manage.py migrate` as shown above.
Each migration is recorded in the `migrations` table with its checksum, so an
applied migration must never be edited.

## Fail2ban integration

To protect against brute-force attacks, configure Fail2ban:

1. Set `FAIL2BAN_LOG_PATH` in `/etc/myportal.env`.
2. Copy the filter `deploy/fail2ban/myportal-auth.conf` to `/etc/fail2ban/filter.d/`.
3. Copy the jail `deploy/fail2ban/myportal-auth.local` to `/etc/fail2ban/jail.d/`.
4. Update `logpath` in the jail configuration.
5. Restart Fail2ban: `systemctl restart fail2ban`.

See [Fail2ban](Fail2ban) for details.

## Troubleshooting

**The installer stops at "MyPortal requires MariaDB 10.10 or later".** The
distribution's MariaDB is too old (for example Ubuntu 22.04). Install MariaDB
10.11 from mariadb.org, or use a remote MariaDB server by setting `DB_HOST`,
`DB_USER`, `DB_PASSWORD` and `DB_NAME` in `/etc/myportal.env` (or `.env` for
development), then re-run the installer.

**"Database migration failed; release was not activated".** The serving
release is unchanged. Read the migration error above that message, check the
`DB_*` settings, and re-run `scripts/upgrade.sh`.

**The portal shows the "Welcome to nginx" page.** Another enabled nginx site
claims `default_server` on port 80. Disable it, or add your hostname to
`server_name` in `/etc/nginx/sites-available/myportal.conf`, then run
`sudo nginx -t && sudo systemctl reload nginx`.

**Logins fail right after enabling `ENVIRONMENT=production`.** The portal is
being reached over HTTP. Terminate TLS in front of nginx first (see above).

## Next steps

- [Configure authentication](Authentication-API)
- [Review configuration options](Configuration)
- [Redis](https://github.com/bradhawkins85/MyPortal/blob/main/docs/wiki/getting-started/Redis.md) for multi-worker caching and rate limiting
