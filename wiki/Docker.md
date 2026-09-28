# Running MyPortal with Docker

MyPortal ships a container image for every GitHub release, plus a single
installer/upgrader script. You don't need to clone the repository: download
the script and run it. If you prefer a VM, LXC container or bare-metal server,
see [Setup and Installation](Setup-and-Installation).

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
3. Creates `/opt/myportal-docker` containing `docker-compose.yml` (MyPortal
   and MariaDB 11.4) and the configuration files, with generated secrets
   and database passwords.
4. Starts the stack. It waits until `/readyz` reports the new release;
   database migrations run automatically when the container starts.
5. Installs itself as `/usr/local/bin/myportal-docker`.

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

An upgrade:

1. Updates `myportal-docker` itself to the copy published with the target
   release, so compose changes that ship with a release are applied.
2. Pulls (or builds) the new image **before** stopping anything.
3. Backs up the database to `/opt/myportal-docker/backups/`.
4. Recreates the application container. Migrations run on start, and the
   upgrade waits until `/readyz` reports the new release.
5. If the new release doesn't become healthy, switches back to the previous
   image and tells you which backup to restore if needed. Unattended runs
   then skip that release until you retry it with `--version`.

The application is briefly unavailable while its container is recreated. For
zero-downtime blue/green upgrades, use the VM/LXC installation instead.

Automatic upgrades (daily, at a random time between 02:00 and 04:59):

```bash
sudo myportal-docker auto-upgrade on    # or off
```

Inside the portal, the scheduled "system update" task only reports that Docker
installations are upgraded with this script.

## Day-to-day commands

| Command | Purpose |
| --- | --- |
| `myportal-docker status` | Running release, containers and readiness |
| `myportal-docker logs [--tail 100]` | Application logs (follows by default) |
| `sudo myportal-docker restart` | Apply changes made to `myportal.env` |
| `sudo myportal-docker backup` | Database dump plus uploaded files |
| `sudo myportal-docker restore-db FILE` | Restore a database backup |

## Super administrators

Super administrator rights can be managed from the server, for example to
recover access when no administrator can sign in. `USERNAME` is the email
address the user signs in with (matched case-insensitively).

| Command | Purpose |
| --- | --- |
| `sudo myportal-docker superadmin list` | List users with super administrator rights |
| `sudo myportal-docker superadmin grant USERNAME` | Grant super administrator rights |
| `sudo myportal-docker superadmin revoke USERNAME` | Revoke super administrator rights |

`revoke` refuses to remove the last active super administrator; add `--force`
to do it anyway. Changes apply on the user's next request; no restart is needed.

## Files and data

| Path | Contents |
| --- | --- |
| `/opt/myportal-docker/myportal.env` | Application settings and secrets. Keep a secure copy: losing `TOTP_ENCRYPTION_KEY` makes stored 2FA secrets and encrypted credentials unrecoverable. |
| `/opt/myportal-docker/mariadb.env` | Database container credentials |
| `/opt/myportal-docker/.env` | Installed release, image, port (managed by the script) |
| `/opt/myportal-docker/docker-compose.yml` | Generated; rewritten on upgrade |
| `/opt/myportal-docker/docker-compose.override.yml` | Optional local additions, merged automatically |
| `/opt/myportal-docker/backups/` | Database and file backups (the newest 10 of each are kept) |

Data lives in the Docker volumes `myportal_db_data`, `myportal_uploads`,
`myportal_private_uploads` and `myportal_state`.

Any setting from [`.env.example`](https://github.com/bradhawkins85/MyPortal/blob/main/.env.example) can be added to
`myportal.env`. `DB_HOST`/`DB_PORT` are set by the compose file.

## TLS and reverse proxies

The container serves plain HTTP. To expose MyPortal publicly:

1. Put a TLS-terminating reverse proxy (Caddy, Traefik, nginx, a load
   balancer) in front of it. Consider `install --bind 127.0.0.1 --port 8080`
   so only the proxy can reach it.
2. In `myportal.env`, set `PORTAL_URL=https://portal.example.com`,
   `ENVIRONMENT=production` (Secure-only cookies, HSTS), and `TRUSTED_PROXIES`
   to the proxy's address.
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
