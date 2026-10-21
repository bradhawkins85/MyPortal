# Configuration Reference

MyPortal loads configuration from environment variables declared in a `.env`
file. The template at `.env.example` lists every supported key alongside
recommended defaults. Copy the template to `.env` (or point your process manager
at a dedicated path) and edit values as required for the deployment target.

The [Onboarding Wizard](Onboarding%20Wizard.md) (`scripts/onboarding_wizard.py`,
or `myportal-docker setup` on Docker) prompts for these values feature by
feature, and `--check` verifies an existing file.

For integration-specific guidance refer to the dedicated documentation under
`docs/`. For example, [docs/xero.md](xero.md) outlines the callback URL and
credential requirements for the Xero module.

## MySQL connection pools

Each application worker has a query pool and a separate advisory lock pool.
Scheduled jobs hold advisory locks while running queries and external API calls;
separating these connections prevents the jobs from occupying every query slot.

```dotenv
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=100
DB_LOCK_POOL_MAX_SIZE=10
DB_POOL_WAIT_TIMEOUT=10
```

These are the defaults, including for existing `.env` files without these keys.
Pool sizes are bounded positive integers (minimum query size can be zero), and
the query minimum must not exceed its maximum. Restart or roll the application
workers after changing them. An existing explicit `DB_POOL_MAX_SIZE=50` continues
to override the default; change it to `100` in the service's environment file to
use the larger pool. For the templated systemd service, check `/etc/myportal.env`
and any `/etc/myportal.blue.env` or `/etc/myportal.green.env` overrides. SQLite
does not use these pools.

MariaDB's `max_connections` is a server-wide ceiling, independent of these
per-worker limits. For example, the two-worker systemd service with the defaults
can use up to 220 main-database connections, or 440 while both blue/green slots
are running, plus any configured event-log and stats pools.
Allow additional capacity for other clients and overlapping workers during
blue/green upgrades. A 16 GB host with `max_connections=2500` does not need
2,500 connections in each application pool; memory usage also depends on
MariaDB's buffer pool, per-query buffers, and the other services on that host.

If acquisition timeouts persist, the application warning reports pool size,
free slots, and maximum size. At the time of the error, inspect MariaDB:

```sql
SHOW GLOBAL STATUS WHERE Variable_name IN
  ('Threads_connected', 'Threads_running', 'Max_used_connections');
SHOW FULL PROCESSLIST;
SHOW ENGINE INNODB STATUS;
```

Look for slow queries and blocked transactions before increasing pool sizes.
`DB_POOL_WAIT_TIMEOUT` bounds the wait for a pool connection; increasing it
does not fix exhausted connections or query bottlenecks. The named lock's own
timeout remains separate from its pool acquisition timeout.

## Outbound email sender

Set `COMPANY_NAME` in `.env` to the name recipients should see on outbound
emails, including client onboarding invitations. For example:

```dotenv
COMPANY_NAME="My Company"
SMTP_FROM=support@mydomain.com
```

The default sender becomes `My Company <support@mydomain.com>`. The address
comes from `SMTP_FROM`, falling back to `SMTP_USER`; authentication still uses
`SMTP_USER`. This applies to SMTP relay and SMTP2Go API delivery. An explicit
sender override takes precedence. Leave `COMPANY_NAME` blank to keep the
existing sender format. Restart the application after updating `.env`.

Use `{{env.company.name}}` in message templates to include this organisation
name. `{{company.name}}` continues to refer to the client company.

## UI Auto Refresh

`ENABLE_AUTO_REFRESH` controls whether browser clients automatically poll the
server for new data. When set to `true`, list and dashboard views schedule
background refreshes so agents see near real-time updates without reloading the
page. Leave the flag at its default value of `false` if you prefer to refresh
manually or want to reduce background traffic for constrained environments.

The deployment helpers (`scripts/install_production.sh`,
`scripts/install_development.sh`, `scripts/upgrade.sh`, and
`scripts/restart.sh`) seed the flag into `.env` if the file was created before
the option existed. Override the value directly in `.env` or through your
process manager's secret store.

## Asset types

`ASSET_TYPE_MODE` controls the type picker on **Create asset** (`/assets/new`)
and when editing a manually created asset:

- `auto` (default) – choose from the built-in IT catalogue (routers, switches,
  laptops, printers and so on). Each type has its own icon and network map row.
- `custom` – the built-in catalogue plus any type typed into the box. Typing a
  catalogue name (for example "router") still uses the catalogue type.
- `manual` – no built-in types. Use this when MyPortal is not tracking IT
  equipment.

In `custom` and `manual` modes the box suggests the types already entered for
the company, and a typed type that differs only in case or spacing reuses the
existing spelling, so duplicates such as "Forklift" and "forklift " are not
created. Typed-in types are drawn with the generic "Other" icon on the network
map and appear under **Custom** in its type filter. Assets synchronised from
integrations always use the built-in catalogue.

## Component availability

All bundled components are available by default. Operators who need a reduced
deployment can exclude components with `DISABLED_FEATURE_PACKS` and
`DISABLED_MODULES`. See [Component Deactivation](Component%20Deactivation.md)
for valid slugs, dependency and precedence rules, examples, and the deployment
verification checklist.
