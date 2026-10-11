# RMM Event Logs

Windows devices with the [RMM agent](RMM%20Scripts.md) send their Windows event logs to MyPortal.
Every device sends its **Application** and **System** logs; technicians can add more logs for a
whole company or for one device. Browse them at **Scripts → Event logs** (`/rmm/event-logs`).

Event logs are high volume, so they are stored in a **separate database**, not MyPortal's main
one, and are left out of backups unless you ask for them.

## Setting up the event log database

Add these to the environment file (`/etc/myportal.env` on bare metal, `myportal.env` on Docker)
and restart MyPortal:

| Variable | Default | Description |
|----------|---------|-------------|
| `EVENT_LOG_DB_HOST` | empty | MySQL or MariaDB host for event logs |
| `EVENT_LOG_DB_PORT` | `3306` | Port |
| `EVENT_LOG_DB_USER` | empty | User |
| `EVENT_LOG_DB_PASSWORD` | empty | Password |
| `EVENT_LOG_DB_NAME` | empty | Database name (letters, digits, `_` and `$`) |
| `EVENT_LOG_DB_BACKUP` | `false` | `true` also backs up the event log database (see [Backups](Backups.md)) |
| `EVENT_LOG_RETENTION_DAYS` | `30` | Days of entries to keep; a daily job (03:30) deletes older ones. `0` keeps everything |

While `EVENT_LOG_DB_HOST`, `EVENT_LOG_DB_USER` or `EVENT_LOG_DB_NAME` is empty, event log
collection is **off**: agents are told not to send anything, and nothing is written to the main
database instead.

On startup MyPortal connects, creates the database if it is missing (when the user is allowed
to) and creates any missing tables. If it cannot connect, MyPortal still starts, the Event logs
page shows the error and MyPortal tries again every minute. The simplest set-up is a second
database on the same server, with its own user:

```sql
CREATE DATABASE myportal_eventlogs CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'myportal_events'@'%' IDENTIFIED BY 'a-strong-password';
GRANT CREATE, ALTER, INDEX, SELECT, INSERT, DELETE ON myportal_eventlogs.* TO 'myportal_events'@'%';
```

On Docker, run these in the bundled database with `myportal-docker` (or point
`EVENT_LOG_DB_HOST` at another server) and use `EVENT_LOG_DB_HOST=db`.

## Choosing logs

On the Event logs page, **Logs collected** lists extra logs, one per line:

- **Extra logs for every device** in the company you are working in.
- **Extra logs for this device**, beside each device with the RMM agent.

A device collects Application and System, then its company's extra logs, then its own. Use the
log's full name as shown in Event Viewer → the log's **Properties** → **Full Name**, for example
`Security`, `Windows PowerShell` or `Microsoft-Windows-PowerShell/Operational`. Changing the logs
needs write access to Scripts (`menu.rmm_scripts`).

## How the agent sends logs

About 30 seconds after it starts, then every minute, the agent asks MyPortal which logs to
collect and sends anything new, in batches of up to 500 entries. It remembers the last record it
sent from each log (`eventlog-cursors.json` in its state folder), so nothing is lost while the
device is offline or MyPortal is unreachable. A log it has never sent starts from the last 24
hours. When a log is cleared on the device the agent notices and starts that log again.

The agent reads logs with Windows' `wevtutil`, which gives each entry its message as Event Viewer
shows it. Logs that do not exist on a device are reported once in the agent's log and skipped.
macOS and Linux agents do not send event logs.

## API

- `GET /api/rmm/event-logs` the current company's entries, newest first. Filters: `asset_id`,
  `log`, `level` (`1` critical, `2` error or worse, `3` warning or worse), `event_id`, `q` (text
  in the message or source) and `limit` (up to 1000). Answers 503 while collection is off.
- Agents use `GET /api/rmm/agent/event-logs/config` and `POST /api/rmm/agent/event-logs`. An
  entry is stored once: resending a batch is harmless. Entries from logs the device is not set to
  collect are skipped.
