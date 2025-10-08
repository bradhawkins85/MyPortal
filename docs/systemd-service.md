# Running MyPortal as a systemd Service

The instructions below describe how to install MyPortal as a hardened
`systemd` service on a Linux host. The goal is to provide a reproducible
setup that keeps the application running, applies migrations at startup,
and limits exposure in the event of a compromise.

## 1. Create a dedicated service account

Run these commands as `root` (or with `sudo`) to create an unprivileged
user and group. The service user owns the application files and has no
shell access for improved security.

```bash
useradd --system --create-home --shell /usr/sbin/nologin myportal
mkdir -p /opt/myportal
chown myportal:myportal /opt/myportal
```

Clone the repository or deploy your application artifact into
`/opt/myportal`. Copy `.env.example` to `.env` and update secrets and
connection strings as needed.

## 2. Prepare the virtual environment

Switch to the service account and bootstrap the Python environment. This
keeps dependencies isolated from the system Python install.

```bash
sudo -u myportal -H bash -c '
  cd /opt/myportal && \
  python3 -m venv .venv && \
  source .venv/bin/activate && \
  python scripts/bootstrap_venv.py
'
```

Re-run the bootstrap script whenever you update dependencies. It will
ensure `pip install -e .` runs inside the managed environment.

## 3. Store environment variables securely

Create an environment file that only the service account can read. This
avoids embedding credentials in the unit file.

```bash
cat <<'ENV' | sudo tee /etc/myportal.env
SESSION_SECRET=change-me
TOTP_ENCRYPTION_KEY=change-me
DATABASE_URL=mysql+aiomysql://user:pass@127.0.0.1:3306/myportal
REDIS_URL=redis://127.0.0.1:6379/0
ENV
chmod 600 /etc/myportal.env
chown myportal:myportal /etc/myportal.env
```

Update the values to match your deployment. Keep secrets strong and do
not commit the file to version control.

## 4. Create the systemd unit

Write the following unit file to `/etc/systemd/system/myportal.service`:

```ini
[Unit]
Description=MyPortal customer portal
After=network-online.target mysql.service redis.service
Wants=network-online.target

[Service]
Type=notify
User=myportal
Group=myportal
WorkingDirectory=/opt/myportal
EnvironmentFile=/etc/myportal.env
ExecStart=/opt/myportal/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=5
# Sandbox the process as much as possible without breaking migrations or file access.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/myportal

[Install]
WantedBy=multi-user.target
```

Key points:

- `Type=notify` allows Uvicorn to report readiness to systemd. Remove the
  directive if you are not using Uvicorn's `--factory` or `--lifespan`
  support.
- The service runs as the dedicated `myportal` user with a restricted
  home directory and `NoNewPrivileges` enabled.
- `ProtectSystem` and `ProtectHome` restrict filesystem access while
  keeping `/opt/myportal` writable for logs and migrations.

## 5. Enable and start the service

Reload systemd so it recognises the new unit, then enable and start it.

```bash
systemctl daemon-reload
systemctl enable --now myportal.service
```

Systemd will apply database migrations each time the service starts,
thanks to the startup hook in `app/main.py`.

## 6. Monitor and maintain the service

Check the service status and logs with:

```bash
systemctl status myportal.service
journalctl -u myportal.service -f
```

When deploying updates, pull the latest code, reinstall dependencies if
needed, and reload the service:

```bash
cd /opt/myportal
sudo -u myportal -H git pull origin main
sudo -u myportal -H /opt/myportal/.venv/bin/pip install -e .
systemctl restart myportal.service
```

If the service fails to start, `systemctl status` will show the most
recent errors. Systemd automatically restarts the process after five
seconds; adjust `RestartSec` as needed for your environment.

## 7. Optional hardening

- Place the application behind a reverse proxy (for example nginx) that
  terminates TLS and sets secure headers.
- Configure a dedicated firewall rule to limit inbound traffic to port
  8000 (or whichever port you expose via the reverse proxy).
- Use `tmpfiles.d` to rotate Loguru output if you configure file-based
  logging.
- Integrate with your monitoring stack to alert on repeated restarts or
  health check failures.

With these steps in place, MyPortal runs as a resilient service that can
be managed with the same tooling as the rest of your infrastructure.
