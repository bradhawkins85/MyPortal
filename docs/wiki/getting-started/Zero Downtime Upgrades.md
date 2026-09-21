# Zero-downtime immutable releases

Production updates use two systemd slots and immutable, revision-named release
directories. The Git checkout is a **control checkout**, never a serving tree.

## Layout

* `/opt/myportal/releases/<git-sha>` contains application code, static assets,
  templates, and a release-local `.venv`. A prepared directory is made
  read-only before it is published.
* `/opt/myportal/shared` contains mutable state and application data. Each
  release's `var` symlink points here.
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

Install `deploy/systemd/myportal@.service` and
`deploy/nginx/myportal-bluegreen.conf`, then run:

```console
sudo /opt/myportal/control/scripts/upgrade.sh --rolling
```

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
