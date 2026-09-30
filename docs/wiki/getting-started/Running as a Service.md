# Running MyPortal as a systemd Service

`scripts/install_production.sh` creates and manages every service described
here; nothing needs to be configured by hand. This page explains what it sets
up and how to operate it. For installation steps, see
[Setup and Installation](Setup%20and%20Installation.md).

## What runs where

| Unit | Purpose |
| --- | --- |
| `myportal@blue.service` / `myportal@green.service` | The two application slots (ports 8001 and 8002, bound to 127.0.0.1). Each runs `uvicorn` from the release its `/opt/myportal/instances/<slot>` link points to. nginx routes traffic to exactly one of them. |
| `nginx.service` | Public listener on port 80, using `/etc/nginx/sites-available/myportal.conf` and the active-slot include `/etc/nginx/conf.d/myportal-active.inc`. |
| `mariadb.service` | Local database, when `DB_HOST` is `localhost`. |
| `/etc/cron.d/myportal-update` | Runs `scripts/process_update_flag.sh` every minute to apply updates requested from the admin UI. |

The unit template is `deploy/systemd/myportal@.service`, and `upgrade.sh`
reinstalls it on every deployment. A slot with no assigned release is skipped
(`ConditionPathExists`), so after the first deployment only one slot is
running. That is expected.

Hardening applied by the unit:

- Runs as the unprivileged `myportal` account.
- `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=full` and
  `ProtectHome=true`.
- Kernel and namespace hardening: `ProtectKernelTunables`,
  `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`,
  `RestrictSUIDSGID`, `RestrictNamespaces`, `RestrictRealtime`,
  `LockPersonality`, `SystemCallArchitectures=native`, an empty
  `CapabilityBoundingSet`, and `RestrictAddressFamilies` limited to
  `AF_UNIX AF_INET AF_INET6 AF_NETLINK`. `MemoryDenyWriteExecute` is not set
  because cffi (WeasyPrint) and the PowerShell fallback need it off.
- Release directories are read-only. Only `/opt/myportal/shared` (uploads and
  state) and `/var/log/myportal` are writable by the service.
- Configuration comes from `/etc/myportal.env` (`root:myportal`, mode 0640).
- Uvicorn listens on `127.0.0.1` only, so clients cannot bypass nginx (and
  fail2ban) by connecting to ports 8001/8002 directly. If nginx runs on a
  different host, set `MYPORTAL_BIND_HOST=0.0.0.0` in `/etc/myportal.env`,
  firewall 8001/8002 to the proxy, and restart both slots. The address must
  still accept loopback connections because `upgrade.sh` checks `/readyz` on
  `127.0.0.1`.

## Day-to-day operations

```bash
# Health of the serving release (reports the deployed Git revision)
curl http://localhost/readyz

# Which slot is serving
cat /etc/nginx/conf.d/myportal-active.inc

# Status and logs
systemctl status 'myportal@*'
journalctl -u 'myportal@*' -f

# Apply configuration changes made in /etc/myportal.env
sudo systemctl restart myportal@blue.service myportal@green.service

# Deploy the latest origin/main (runs scripts/upgrade.sh from the control
# checkout recorded as MYPORTAL_CONTROL_CHECKOUT in /etc/myportal.env)
sudo myportal-upgrade
```

`APP_UPGRADE_MODE`, `SYSTEMD_SERVICE_NAME` and `APP_RESTART_COMMAND` are only
honoured by the legacy `scripts/restart.sh` helper. Every update of a
production server uses the blue/green procedure in `upgrade.sh`; see
[Zero Downtime Upgrades](Zero%20Downtime%20Upgrades.md).

## TLS and hostname

The bundled nginx site answers any hostname on port 80 (`server_name _;`). To
use a real hostname:

1. Edit `server_name` in `/etc/nginx/sites-available/myportal.conf`.
2. Terminate TLS, for example with `certbot --nginx`.
3. Set `PORTAL_URL=https://…` and `ENVIRONMENT=production` in
   `/etc/myportal.env`.
4. Restart both slots as shown above.

`upgrade.sh` reinstalls the nginx site from the release on each deployment. If
you customise the site, keep your changes in a separate file (for example a TLS
server block in `/etc/nginx/sites-available/myportal-tls.conf` that proxies to
`http://myportal_app`), so an upgrade does not overwrite them.

## Development service

`sudo scripts/install_development.sh` creates `myportal-dev.service` instead.
It runs `uvicorn` from the developer's checkout and `.venv`, as the developer's
user, on port 8000 (`DEV_SERVER_PORT` in `.env` changes this). It uses the
checkout's `.env` and never touches the production units, so a development and
a production installation can share one host.

```bash
systemctl status myportal-dev.service
journalctl -u myportal-dev.service -f
```

## Optional hardening

- Allow only ports 80/443 through the firewall. The slots listen on
  8001/8002 for the deployment coordinator's local checks; block those
  ports from outside.
- Define `FAIL2BAN_LOG_PATH` in `/etc/myportal.env` and install the bundled
  Fail2ban filter and jail (see [Fail2ban](Fail2ban%20Setup.md)).
- Alert on repeated restarts of `myportal@*.service`, or on `/readyz`
  returning a non-200 response.
