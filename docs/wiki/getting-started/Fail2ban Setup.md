# Fail2ban Integration

MyPortal can feed login activity into Fail2ban so repeated authentication
failures are automatically blocked. The integration relies on structured log
messages emitted by the application whenever a login succeeds or fails.

## 1. Enable authentication logging

1. Open your `.env` file and set a path for the dedicated authentication log:
   ```bash
   FAIL2BAN_LOG_PATH=/var/log/myportal/auth.log
   ```
2. Restart the application. On startup, MyPortal will create the parent
   directory if it is writable and begin mirroring login events to the file
   alongside standard output.

Each failed login writes a line similar to:
```
2025-10-15T12:18:28.123+0000 | ERROR | AUTH LOGIN FAIL email=user@example.com ip=203.0.113.10 reason=invalid_credentials
```
Successful logins are also recorded with `AUTH LOGIN SUCCESS` entries to assist
with forensic reviews without affecting Fail2ban rules.

## 2. Install the Fail2ban filter and jail

Copy the bundled configuration files into the Fail2ban directories on your
host (usually `/etc/fail2ban`). Adjust the `logpath` to match the value set in
`FAIL2BAN_LOG_PATH` if you choose a different location.

```bash
sudo cp deploy/fail2ban/myportal-auth.conf /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/myportal-auth.local /etc/fail2ban/jail.d/
```

The filter matches the `AUTH LOGIN FAIL` lines written by the application, while
the jail applies a five-attempt limit over five minutes and bans the offending
IP for one hour by default. Tweak `maxretry`, `findtime`, and `bantime` to suit
your policy.

## 3. Restart Fail2ban and verify

Reload Fail2ban so it picks up the new configuration:

```bash
sudo systemctl restart fail2ban
sudo fail2ban-client status myportal-auth
```

Trigger a test failure (for example by attempting a login with an invalid
password) and confirm that the attempt appears in the jail log. Once the retry
threshold is exceeded, Fail2ban will block the IP address until the ban expires
or is manually removed.

## Troubleshooting

- Ensure the service account running MyPortal can create and write to the log
  file path configured in `FAIL2BAN_LOG_PATH`. When the directory cannot be
  created the application logs a warning and continues writing to stdout.
- If Fail2ban is using the `systemd` backend, set `journalmatch` in the jail to
  match your service unit instead of relying on a file log.
- Keep the authentication log on a persistent volume so Fail2ban can parse
  entries across restarts or container redeployments.
