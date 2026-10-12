# RMM Event Logs

Windows devices with the [RMM agent](RMM%20Scripts.md) send their Windows event logs to MyPortal.
Every device sends its **Application** and **System** logs; technicians can add more logs for a
whole company or for one device. Browse them from **Event logs** in the main menu
(`/rmm/event-logs`), filtered by device, log, level, event ID, source, message text and
date range (UTC days), or on an asset's page under **Event logs**. [Event log rules](#event-log-rules)
drop events you do not want to keep, or run a script when an event arrives.

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

## On the asset page

Each device's page (`/assets/{id}`) has two sections for technicians with Scripts access:

- **Event logs**: the latest 25 entries from the device, filters for log, level, event ID,
  source, message text and a date range (up to 100 matching entries), a link to the Event logs
  page, and the device's extra logs.
- **Event log automations**: every rule that applies to the device (its own, its company's and
  those for every company), with **Add a rule for this device**.

## Event log rules

Open **Event log rules** from the Event logs page (`/rmm/event-logs/rules`). A rule has:

- **Applies to**: one device, every device in the company you are working in, or every company
  (super admins only).
- **Conditions**, all of which must match. Each compares one field of the event: Log, Event ID,
  Level, Source, Computer or Message. Text comparisons ignore case and can be *is*, *is not*,
  *is one of* / *is not one of* (comma separated), *contains*, *does not contain* or *starts
  with*. Event ID takes *is*, *is not*, *is one of* and *is not one of*; Level takes *is*, *is
  not* and *is at least* (that level or more severe). A rule needs at least one condition.
- **Then**:
  - **Drop the event**: matching events are not stored. Drop rules are checked first, so a
    dropped event never runs a script.
  - **Run a script**: runs an [RMM script](RMM%20Scripts.md) on the device that sent the event.
    It runs at most once per batch the agent sends, using the first matching event, and then not
    again on that device until the **cooldown** (60 minutes by default) has passed. After you
    choose the script and save, its parameters appear; besides the usual variables they can use
    the event's own, such as `{{event.message}}`, `{{event.event_id}}`, `{{event.level}}`,
    `{{event.provider}}`, `{{event.log_name}}`, `{{event.computer}}`, `{{event.record_id}}` and
    `{{event.time_created}}`. Rules for every company can only use scripts in the `Common` folder.
  - **Schedule a script**: runs a script on the device that sent the event later instead of
    straight away, for actions such as a reboot out of hours. Choose **After a delay** (in
    minutes) or **At the next time of day** (HH:MM in the rule's time zone, which defaults to
    `DEFAULT_TIMEZONE`). Its script values work as for **Run a script**, using the event that
    scheduled it. One run waits per rule and device: more matching events while it waits do not
    schedule another. The RMM automation job queues it when it is due; if the rule is turned off,
    changed to another action or deleted first, the waiting run is dropped. The rules list shows
    how many devices have a run waiting.
  - **Create a ticket**: raises a ticket for the company the device belongs to, with the
    device's asset linked and the matching events in its description. While that ticket is not
    closed, later matches from the same rule on the same device are added to it as internal
    updates instead of raising another ticket; once it is closed, the next match raises a new one.

Each rule has an **Action** menu (on the rules page and in the asset page's Event log
automations) to edit it, turn it on or off, or delete it. Drop rules also have **Run now**, which
applies the rule to events already stored: MyPortal shows how many stored events in the rule's
scope match, and deletes them once you confirm. Run now only deletes; it never runs scripts.

Runs a rule starts show **Event log rule** as who started them in run history. The rules list
shows how many events each rule matched and what it last did. Changing rules needs write access
to Scripts (`menu.rmm_scripts`).

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
  in the message or source), `source` (text in the source), `from` and `to` (UTC dates,
  inclusive) and `limit` (up to 1000). Answers 503 while collection is off.
- Agents use `GET /api/rmm/agent/event-logs/config` and `POST /api/rmm/agent/event-logs`. An
  entry is stored once: resending a batch is harmless. Entries from logs the device is not set to
  collect are skipped. The answer counts entries `received`, `stored`, `skipped`, `dropped` by
  rules, script `runs` the rules started, runs `scheduled` for later and new `tickets` they raised.
