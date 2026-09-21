# Zero-downtime immutable releases

Production updates use two systemd slots and immutable, revision-named release
directories. The Git checkout is a **control checkout**, never a serving tree.

## Layout

* `/opt/myportal/releases/<git-sha>` contains application code, static assets,
  templates, and a release-local `.venv`. A prepared directory is made
  read-only before it is published.
* `/opt/myportal/shared` contains mutable state and application data. Each
  release's `var`, `private_uploads`, and `app/static/uploads` paths point here.
  Existing upload data from a legacy single-checkout installation is copied
  into shared storage on first use and is never removed from the old location.
* `/opt/myportal/instances/{blue,green}` independently selects the release for
  each service. `/opt/myportal/current` selects static/error assets exposed by
  nginx and is changed only after cutover succeeds.
* `/etc/nginx/conf.d/myportal-active.inc` is mandatory and contains exactly two
  server declarations: one active and one `down`.

Create the initial include before enabling the nginx site:

```console
sudo install -d -m 0755 /etc/nginx/conf.d
printf '%s\n' \
  'server 127.0.0.1:8001 max_fails=1 fail_timeout=5s;' \
  'server 127.0.0.1:8002 down;' |
  sudo tee /etc/nginx/conf.d/myportal-active.inc
sudo nginx -t
```

Run the upgrade coordinator:

```console
sudo /opt/myportal/control/scripts/upgrade.sh --rolling
```

The coordinator installs `deploy/nginx/myportal-bluegreen.conf` using the
host's `sites-available`/`sites-enabled` layout when present, or `conf.d` on
other nginx installations. It validates the configuration and enables and
starts nginx only after the candidate application instance has passed its
readiness and smoke checks. The manual include creation above remains useful
when validating nginx before the first upgrade, but the coordinator also
creates it automatically.

The upgrade installs or refreshes `deploy/systemd/myportal@.service`, reloads
systemd, and enables both instance units before it starts the inactive slot.
This also upgrades older installations that previously had only
`myportal.service`. The upgrade must therefore run as root (normally via
`sudo`). Release directories are made read-only but remain readable and
traversable by the unprivileged service account. Retrying an older prepared
release repairs root-only directory permissions. A failure before nginx
cutover leaves the existing upstream unchanged.

The release virtual environment is created only after the revision reaches its
final `/opt/myportal/releases/<git-sha>` path because Python console scripts
contain absolute interpreter paths. When retrying a release prepared by an
older updater, the coordinator validates that the release interpreter can
import `uvicorn` and rebuilds only a broken virtual environment.

The systemd instances invoke `uvicorn` with the release interpreter using
`python -m uvicorn`. They do not execute the generated `bin/uvicorn` wrapper,
so a stale console-script shebang from a previously prepared release cannot
prevent the service from starting.

Upload directories are persistent writable storage rather than part of an
immutable release. The upgrade creates them with ownership for the `myportal`
service account and links both upload paths into every prepared release.
Release immutability is enforced with read-only ownership modes rather than a
systemd read-only bind mount, because such a mount also masks writes through
the upload symlinks. The systemd unit explicitly declares the shared tree
writable, while the service account has no write permission on release files.

The deployer fetches (it never pulls or restores), exports the target commit to
a staging directory, installs a private virtual environment, and makes the
release read-only. It points only the inactive slot at that release, starts it,
and requires both `/readyz` and the smoke endpoint to report the expected Git
SHA. It then atomically writes the upstream include, runs `nginx -t`, reloads,
waits `MYPORTAL_DRAIN_SECONDS`, and finally switches `current`.

An error during startup, version validation, smoke testing, nginx validation,
or cutover restores the old upstream, inactive-slot link, and `current` link.
The former active process continues serving throughout preparation and
validation. The previous known-good release is deliberately retained.

## Configuration

The upgrade coordinator reads `/etc/myportal.env` by default, matching the
`EnvironmentFile` used by `myportal@.service`. Set `MYPORTAL_ENV_FILE` only for
a nonstandard deployment; legacy installations without `/etc/myportal.env`
continue to use the control checkout's `.env` file.

`MYPORTAL_READY_TIMEOUT` bounds readiness in seconds,
`MYPORTAL_DRAIN_SECONDS` bounds the drain period, and
`MYPORTAL_SMOKE_PATH` selects an idempotent smoke endpoint. Path overrides for
test or nonstandard installations are listed in `.env.example`. The deployment
user needs narrowly scoped permission to restart `myportal@*.service`, validate
and reload nginx, and atomically replace the upstream include; it does not need
recursive ownership permission.
