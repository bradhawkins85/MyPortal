# Running MyPortal with Docker

MyPortal ships a container image for every GitHub release, plus a single
installer/upgrader script. You don't need to clone the repository: download
the script and run it. If you prefer a VM, LXC container or bare-metal server,
see [Setup and Installation](Setup%20and%20Installation.md).

## Install

On any Linux host with `curl` (Docker is installed for you if it is missing):

```bash
curl -fsSLO https://github.com/bradhawkins85/MyPortal/releases/latest/download/myportal-docker.sh
sudo bash myportal-docker.sh install
```

The installer:

1. Installs Docker Engine, the Compose v2 plugin and buildx from Docker's
   official repository (`download.docker.com`) if they are missing. It
   supports Ubuntu, Debian, Raspberry Pi OS, Fedora, RHEL, CentOS, Rocky
   and AlmaLinux (and their derivatives); on anything else it uses Docker's
   convenience script. If Docker is present but Compose v2 is not, only the
   plugin is added.
2. Finds the latest published GitHub release and pulls
   `ghcr.io/bradhawkins85/myportal:<release>`. If the image can't be pulled,
   it builds the image locally from that release's source archive.
3. Creates `/opt/myportal-docker` containing `docker-compose.yml` (MariaDB
   11.4, the blue and green MyPortal slots and an nginx proxy) and the
   configuration files, with generated secrets and database passwords.
4. Starts the stack. It waits until `/readyz` reports the new release;
   database migrations run automatically when the container starts.
5. Installs itself as `/usr/local/bin/myportal-docker`.

Optionally run `sudo myportal-docker setup` to choose which features are
enabled and configure their settings (see [Onboarding Wizard](Onboarding%20Wizard.md)),
then `sudo myportal-docker restart`.

Then open `http://<host>/` and register. The first account becomes the super
administrator.

Options: `--port 8080` (the default is 80), `--bind 127.0.0.1` (publish only
on localhost, for example behind your own reverse proxy), and
`--version v0.6.0` (install a specific release).

## Upgrade

Upgrades only ever move to a **published GitHub release**. They never deploy
unreleased code from `main`.

```bash
sudo myportal-docker check      # exit code 10 when an upgrade is available
sudo myportal-docker upgrade    # or: upgrade --version v0.6.1
```

Upgrades are blue/green, like the [VM/LXC installation](Zero%20Downtime%20Upgrades.md),
so the portal stays available throughout. MyPortal runs in two application
containers, `app_blue` and `app_green`, that share the database and data
volumes. Only one of them serves: an nginx container (`proxy`) publishes the
HTTP port and forwards to it. An upgrade:

1. Updates `myportal-docker` itself to the copy published with the target
   release, so compose changes that ship with a release are applied.
2. Pulls (or builds) the new image.
3. Backs up the database to `/opt/myportal-docker/backups/`.
4. Starts the new release in the idle slot while the current one keeps
   serving. Migrations run when it starts, and the upgrade waits until its
   `/readyz` reports the new release.
5. Switches the proxy to the new slot with a graceful nginx reload: requests
   already in progress finish on the old slot, new ones go to the new slot.
6. After a short drain (`MYPORTAL_DRAIN_SECONDS`, 10 by default), stops the
   old slot. Its container and image are kept for `rollback`.

If the new release doesn't become healthy, it is stopped and the proxy is
never switched, so users see no interruption. The upgrade tells you which
backup to restore if a partial migration needs undoing. Unattended runs then
skip that release until you retry it with `--version`.

Like the VM/LXC installation, this relies on each release's migrations
working with the release before it, because both run against the same
database for a moment.

### Rolling back

```bash
sudo myportal-docker rollback
```

switches back to the previous release, which the last upgrade left in the
idle slot, the same way: it starts, becomes ready, and only then takes over.
The database is not rolled back; if the older release has problems with the
changes the newer one made, restore the backup taken before the upgrade with
`restore-db`. Unattended and portal upgrades then skip the release you rolled
back from; install it again with `upgrade --version TAG`. `restart` also uses
the idle slot, so after a restart there is no previous release to roll back
to until the next upgrade.

### Installations made before blue/green

Installations made with an earlier `myportal-docker` run a single `app`
container. Their next `upgrade` (or `restart`) converts them: the new release
starts in the blue slot alongside the old container, and the port then moves
to the proxy, which interrupts the portal for a few seconds this one time.
If `docker-compose.override.yml` changes the `app` service, the upgrade stops
before changing anything: move those changes to `app_blue` and `app_green`
(or to `proxy` for published ports and reverse-proxy labels) and rerun it.

Automatic upgrades (daily, at a random time between 02:00 and 04:59):

```bash
sudo myportal-docker auto-upgrade on    # or off
```

### Upgrading from the portal

Super administrators can start an upgrade from **Administration → System
Updates** in the left menu. The page shows the installed and latest release; **Update
now** queues a request and opens a page that follows the upgrade output live
until it succeeds or fails.

The portal never gets root or the Docker socket. It only writes a request file
into its own state volume. A root cron job on the host
(`/etc/cron.d/myportal-docker-requests`, every minute) runs
`myportal-docker process-requests`, which reads and clears the request through
`docker exec` in the serving container, and then runs the same `upgrade --yes`
as the nightly job. It always moves to the latest published release (never a version named by
the portal), with the usual backup and rollback. Progress and the result are
written back to the portal's update history; the host keeps a copy in
`/opt/myportal-docker/web-upgrade.log`.

This is on for new installations, and existing installations turn it on at
their next upgrade. To control it:

```bash
sudo myportal-docker web-upgrades status
sudo myportal-docker web-upgrades off   # or on
```

A request that the host does not pick up within 15 minutes is marked failed in
the portal with a reminder to run `web-upgrades on`.

## Day-to-day commands

| Command | Purpose |
| --- | --- |
| `myportal-docker status` | Running release, containers and readiness |
| `myportal-docker logs [--tail 100]` | Application logs (follows by default) |
| `sudo myportal-docker setup` | Onboarding wizard: enable features and configure `myportal.env` (`--check` to verify it). See [Onboarding Wizard](Onboarding%20Wizard.md) |
| `sudo myportal-docker restart` | Apply changes made to `myportal.env`, without downtime (the idle slot starts with them, then takes over) |
| `sudo myportal-docker rollback` | Switch back to the release before the last upgrade |
| `sudo myportal-docker backup` | Database dump plus uploaded files |
| `sudo myportal-docker restore-db FILE` | Restore a database backup |
| `sudo myportal-docker self-update` | Reinstall `myportal-docker` from the installed release, to get commands it added |

## Super administrators

Super administrator rights can be managed from the server, for example to
recover access when no administrator can sign in. `USERNAME` is the email
address the user signs in with (matched case-insensitively).

| Command | Purpose |
| --- | --- |
| `sudo myportal-docker superadmin list` | List users with super administrator rights |
| `sudo myportal-docker superadmin grant USERNAME` | Grant super administrator rights |
| `sudo myportal-docker superadmin revoke USERNAME` | Revoke super administrator rights |
| `sudo myportal-docker superadmin create USERNAME` | Create a new super administrator |
| `sudo myportal-docker superadmin reset-password USERNAME` | Set a new password for a super administrator |

`revoke` refuses to remove the last active super administrator; add `--force`
to do it anyway. Changes apply on the user's next request; no restart is needed.

`create` and `reset-password` prompt for the new password (at least 12
characters). Add `--generate-password` to have one generated and printed
instead, or pipe the password in on standard input for scripts. `create` also
accepts `--first-name` and `--last-name`.

`reset-password` only works for super administrators, and signs the user out of
every session. If the authenticator app or passkey is lost too, add
`--reset-2fa` to remove them; two-factor sign-in is then set up again at the
next sign-in. For example, to regain access when the only administrator is
locked out:

```bash
sudo myportal-docker superadmin reset-password admin@example.com --reset-2fa
```

## Verifying user accounts

When a user cannot receive the verification email sent at sign-up (for
example because outgoing email is not configured yet), verify the account from
the server instead:

```bash
sudo myportal-docker user verify user@example.com
```

This makes the same change as the emailed link: the email address is marked
verified and the account is activated. An account that is already verified but
deactivated was disabled on purpose, so it is left deactivated; reactivate it
in the portal instead.

Every change made with the `superadmin` and `user` commands is recorded in the audit log with the
source `myportal-docker`.

## Files and data

| Path | Contents |
| --- | --- |
| `/opt/myportal-docker/myportal.env` | Application settings and secrets. Keep a secure copy: losing `TOTP_ENCRYPTION_KEY` makes stored 2FA secrets and encrypted credentials unrecoverable. |
| `/opt/myportal-docker/mariadb.env` | Database container credentials |
| `/opt/myportal-docker/.env` | Installed release, image, port (managed by the script) |
| `/opt/myportal-docker/docker-compose.yml` | Generated; rewritten on upgrade |
| `/opt/myportal-docker/docker-compose.override.yml` | Optional local additions, merged automatically |
| `/opt/myportal-docker/proxy/` | nginx configuration; generated, rewritten on upgrade and restart. `active-slot.inc` names the serving slot |
| `/opt/myportal-docker/backups/` | Database and file backups (the newest 10 of each are kept) |

Data lives in the Docker volumes `myportal_db_data`, `myportal_uploads`,
`myportal_private_uploads` and `myportal_state`.

Any setting from [`.env.example`](../../../.env.example) can be added to
`myportal.env`. `DB_HOST`/`DB_PORT` are set by the compose file.

## TLS and reverse proxies

The container serves plain HTTP. To expose MyPortal publicly:

1. Put a TLS-terminating reverse proxy (Caddy, Traefik, nginx, a load
   balancer) in front of it. Consider `install --bind 127.0.0.1 --port 8080`
   so only the proxy can reach it.
2. In `myportal.env`, set `PORTAL_URL=https://portal.example.com`,
   `ENVIRONMENT=production` (Secure-only cookies, HSTS), and `TRUSTED_PROXIES`
   to the address your proxy connects from, as seen by Docker (with
   `--bind 127.0.0.1`, usually the Docker bridge gateway such as
   `172.17.0.1`, or the proxy's own address). Only requests from those
   addresses may set the client address (`X-Forwarded-For`) and scheme
   (`X-Forwarded-Proto`); the bundled nginx proxy applies this, and the
   application containers trust only the bundled proxy.
3. Run `sudo myportal-docker restart`.

## Settings for special environments

These environment variables change the script's behaviour:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MYPORTAL_DIR` | `/opt/myportal-docker` | Installation directory |
| `MYPORTAL_IMAGE_REPO` | `ghcr.io/bradhawkins85/myportal` | Where release images are pulled from |
| `MYPORTAL_DB_IMAGE` | `mariadb:11.4` | Database image (MariaDB 10.10 or newer) |
| `MYPORTAL_BUILD_CA_FILE` | – | CA bundle for local builds behind a TLS-inspecting proxy. It's passed as a build secret and never stored in the image. |
| `MYPORTAL_BUILD_NETWORK` | – | Network for local builds, e.g. `host` when the proxy only listens on localhost |
| `MYPORTAL_BASE_IMAGE` | `ubuntu:24.04` | Base image for local builds (e.g. a registry mirror) |
| `MYPORTAL_HEALTH_TIMEOUT` | `600` | Seconds to wait for a release to become ready |
| `MYPORTAL_DRAIN_SECONDS` | `10` | Seconds the old slot keeps running after the proxy switches, so its requests can finish |
| `MYPORTAL_PROXY_IMAGE` | `nginx:1.28-alpine` | Proxy image for new installations and conversions (nginx 1.27.3 or newer); recorded as `PROXY_IMAGE` in `.env` |

## Maintainers: publishing images

Publishing a GitHub release runs `.github/workflows/docker-release.yml`. The
workflow:

- builds the image
- smoke-tests it against MariaDB (readiness, first-user registration, PDF
  rendering)
- pushes `ghcr.io/bradhawkins85/myportal:<tag>` for amd64 and arm64, and
  `:latest` for full releases
- attaches `myportal-docker.sh` to the release

After the first run, make the `myportal` package **public** in the package
settings on GitHub, so hosts can pull it without logging in. Until then,
installs and upgrades still work, but they build the image locally, which is
slower. Releases published before Docker support was added have no
Dockerfile, so they can't be installed with Docker.
