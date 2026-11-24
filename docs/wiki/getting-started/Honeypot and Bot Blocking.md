# Honeypot and Bot Blocking

Real people only reach MyPortal by following links inside the portal, and
devices only talk to it through the Tray and RMM agents. Anything else asking
for a page or API endpoint that does not exist is almost always a bot scanning
for weaknesses. MyPortal spots these requests and writes them to the Fail2ban
log, and Fail2ban blocks the sender's IP address with iptables.

> **Before you start:** set up the [Fail2ban Integration](Fail2ban) first.
> The honeypot uses the same `FAIL2BAN_LOG_PATH` log file.

## What gets detected

MyPortal lets every request run as normal. When a request ends in an error on
a path that **no page, API endpoint or static folder serves**, it is logged in
one of two ways:

| Log line | When | Default Fail2ban response |
| --- | --- | --- |
| `HONEYPOT TRAP` | The path is a well-known scanner target that a Python portal never serves: PHP/ASP/JSP scripts (`/wp-login.php`), dotfiles (`/.env`, `/.git/config`, `/.aws/credentials`), admin tools (`/phpmyadmin`, `/cgi-bin/`, `/actuator`), backup files (`/backup.sql`) and path traversal (`../`). | Blocked on the **first** hit for 1 day; repeat offenders are blocked for longer each time, up to 4 weeks. |
| `HONEYPOT PROBE` | Any other unknown page or API endpoint, for example `/api/v1/users` or `/admin/config`. | Blocked after **10** hits in 10 minutes, for 1 hour (longer for repeat offenders, up to a week). |

So that genuine users are not caught, these are **never** logged:

- Paths the portal serves, even when the answer is "not found" (for example a
  ticket that was deleted, or a missing file under `/static/`).
- Anyone signed in to the portal.
- The agent APIs (`/api/tray/…`, `/api/rmm/…`, `/api/agent/…`,
  `/api/health-checks/…`), so a newer agent calling an endpoint an older
  portal does not have yet cannot get a customer's office blocked.
- Files browsers ask for automatically: `/favicon.ico`, `/robots.txt`,
  `/apple-touch-icon*.png`, `/.well-known/…` and similar.
- Rate-limit (429) and maintenance (503) responses.

Example log lines:

```
2026-11-24T10:00:00.000+00:00 | WARNING | - | - | honeypot | HONEYPOT TRAP ip=203.0.113.10 rule=dotfile status=404 method=GET path=/.env
2026-11-24T10:00:02.000+00:00 | WARNING | - | - | honeypot | HONEYPOT PROBE ip=203.0.113.10 status=404 method=GET path=/api/v1/users
```

The IP address always comes straight after the tag, and the requested path is
cleaned before it is written, so a bot cannot get a different address blocked
by putting `ip=…` in its URL.

## 1. Check the application settings

In MyPortal's `.env`:

```bash
FAIL2BAN_LOG_PATH=/var/log/myportal/auth.log
HONEYPOT_ENABLED=true
```

`HONEYPOT_ENABLED` is on by default. Restart MyPortal after changing either
value.

**Behind a reverse proxy (nginx):** set `TRUSTED_PROXIES` (for example
`TRUSTED_PROXIES=127.0.0.1,::1`) so MyPortal logs the visitor's real address
instead of the proxy's. Without it every line shows `127.0.0.1`, which the
jails ignore, and nothing is ever blocked.

**Behind a CDN or load balancer on another host (for example Cloudflare):**
iptables on the MyPortal server only sees the CDN's addresses, so blocking
them would cut off everyone. Use a Fail2ban action that blocks at the CDN
instead (for example Fail2ban's `cloudflare` action) rather than
`iptables-multiport`.

## 2. Install the filters and jails

```bash
sudo cp deploy/fail2ban/myportal-honeypot.conf /etc/fail2ban/filter.d/
```

```bash
sudo cp deploy/fail2ban/myportal-probe.conf /etc/fail2ban/filter.d/
```

```bash
sudo cp deploy/fail2ban/myportal-honeypot.local /etc/fail2ban/jail.d/
```

Edit `/etc/fail2ban/jail.d/myportal-honeypot.local` and set both `logpath`
lines to your `FAIL2BAN_LOG_PATH` if it is not `/var/log/myportal/auth.log`.
Both jails use `banaction = iptables-multiport` on ports 80 and 443. On hosts
that use nftables, change it to `nftables-multiport`.

If many staff share one office IP and you see them blocked, raise `maxretry`
in the `[myportal-probe]` jail rather than turning the honeypot off.

## 3. Start and verify

```bash
sudo systemctl restart fail2ban
```

```bash
sudo fail2ban-client status myportal-honeypot
```

```bash
sudo fail2ban-client status myportal-probe
```

Check the filters match your log file (each should report at least one match
once a bot has visited):

```bash
sudo fail2ban-regex /var/log/myportal/auth.log /etc/fail2ban/filter.d/myportal-honeypot.conf
```

```bash
sudo fail2ban-regex /var/log/myportal/auth.log /etc/fail2ban/filter.d/myportal-probe.conf
```

To test the block end to end, request `/.env` from a machine you can afford to
lock out for a day (not the one you are managing the server from), then
confirm its address appears under *Banned IP list* and then unblock it as
described below.

## Removing an IP address from the block list

If someone who should have access has been blocked, first find which jail
holds the address:

```bash
sudo fail2ban-client banned 203.0.113.10
```

Remove it from a single jail:

```bash
sudo fail2ban-client set myportal-probe unbanip 203.0.113.10
```

Or remove it from every jail at once:

```bash
sudo fail2ban-client unban 203.0.113.10
```

Fail2ban deletes the matching iptables rule straight away. Confirm the address
no longer appears in the chain:

```bash
sudo iptables -L f2b-myportal-probe -n --line-numbers
```

(The honeypot jail's chain is `f2b-myportal-honeypot`.)

**Avoid deleting the iptables rule by hand.** Fail2ban keeps its own record of
bans and puts them back after a restart, so a rule removed with `iptables -D`
returns the next time Fail2ban starts. Always unban through
`fail2ban-client`.

If the same person keeps being blocked, find out why before going further:
search the log for their address to see which paths they requested.

```bash
sudo grep "ip=203.0.113.10 " /var/log/myportal/auth.log
```

A bookmark to a page that no longer exists, or an out-of-date browser
extension, is the usual cause; fixing that is better than allowlisting.

## Allowlisting an IP address (not recommended)

> **Not recommended.** An allowlisted address is never blocked by these jails,
> even if it is later compromised or shared with an attacker (for example a
> café, hotel or mobile carrier address). Prefer unblocking the address and
> fixing the cause. Only allowlist fixed addresses you control, such as your
> own office's static IP or a monitoring service.

Add the address (or CIDR range) to the `ignoreip` line in **both** jail
sections of `/etc/fail2ban/jail.d/myportal-honeypot.local`, separated by
spaces. Keep the existing localhost entries:

```ini
[myportal-honeypot]
ignoreip = 127.0.0.1/8 ::1 203.0.113.10

[myportal-probe]
ignoreip = 127.0.0.1/8 ::1 203.0.113.10
```

To allowlist it for only one jail, change only that jail's line. (These lines
apply to the MyPortal jails only; an `ignoreip` in the `[DEFAULT]` section of
`/etc/fail2ban/jail.local` applies to every jail on the server, SSH included.)

Reload Fail2ban and unban the address if it is currently blocked:

```bash
sudo fail2ban-client reload
```

```bash
sudo fail2ban-client unban 203.0.113.10
```

Check the address is now ignored:

```bash
sudo fail2ban-client get myportal-probe ignoreip
```

To add an address temporarily without editing files (lost when Fail2ban
restarts):

```bash
sudo fail2ban-client set myportal-probe addignoreip 203.0.113.10
```

Remove it again with `delignoreip`, and delete it from the `ignoreip` line as
soon as it is no longer needed.

## Turning the honeypot off

Set `HONEYPOT_ENABLED=false` in `.env` and restart MyPortal to stop logging,
or disable just the blocking with `enabled = false` in either jail and run
`sudo fail2ban-client reload`.
