# RMM Checks

Checks watch devices with the [RMM agent](RMM%20Scripts.md) and act when something goes wrong.
Manage them from **Checks** in the main menu (`/rmm/checks`); each asset's page shows the checks
that reach it under **Checks**, with their latest results, and charts of the device's stats under
**Machine stats**.

## Types of check

| Check | Fails when | Measured by |
|-------|------------|-------------|
| **Disk space** | A drive's free space is below (or above) a number of GB or a percentage. Use a drive letter such as `C:`, a mount point such as `/var`, or `*` for every fixed drive. | Agent |
| **Ping** | Pinging a host or IP address loses at least the chosen share of its four packets (100% by default, so only when nothing answers), or the average reply takes longer than the chosen time. | Agent |
| **CPU load** | CPU use is over a percentage, for example over 80% for 30 minutes. | Agent |
| **Free memory** | Free memory is below a number of GB or a percentage. | Agent |
| **Windows service** | A service is not running (or not stopped). On Linux the name is a systemd unit. | Agent |
| **Network throughput** | Data received, sent or both, on one interface or all of them together, is above (or below) a number of Mbps. | Agent |
| **Script result** | A Gitea script, run on the device every few minutes, does not return what you expect: an exit code, or output that contains, does not contain or exactly matches some text. | Server queues the script |
| **Agent offline** | The agent has not checked in for a number of minutes, for example 30 for a server. | Server |

The agent measures CPU and network throughput as the average over each minute and reports once a
minute. Every check except Agent offline has a **duration**: how long a problem must last before
the check fails. With 0 it fails on the first bad result. Until then the device shows the check as
**Problem**, with the time the problem started. For Agent offline the duration is the time since
the last check-in (at least 5 minutes).

A **Windows service** check can also have the agent fix the service itself: start or restart a
service that should be running, or stop one that should be stopped. The agent does that as soon as
it sees the service in the wrong state. That minute still counts as a problem, so with a duration
of 0 the check also raises its alert; with a longer duration a quick fix raises nothing.

CPU, memory, disk and network are measured on Windows, Linux and macOS; ping on all three; service
checks on Windows and Linux.

## Who a check applies to

Like [event log rules](RMM%20Event%20Logs.md#event-log-rules), a check applies to **every company**
(super admins only), **every device in a company**, or **one device**. When checks at different
levels watch the same thing (the same type and drive, ping target, service, interface or script),
the most specific level wins: a device's own check replaces its company's, which replaces one for
every company. A device's own check that is **off** switches the thing off for that device. On an
asset page, **Change for this device** on a company or every-company check copies it as the
device's own, ready to change or turn off. Several checks at the same level all run, for example a
ticket at 15% free space and a clean-up script at 5%.

## What happens when a check fails

Checks use the same actions as event log rules:

* **Show the result only**: the result shows on the asset page and the Checks page.
* **Run a script**: queue a Gitea script on the device. Its values can use
  `{{check.name}}`, `{{check.type}}`, `{{check.target}}`, `{{check.result}}` and
  `{{check.device}}` as well as the usual variables. While the check keeps failing the script runs
  again every **cooldown** minutes; a cooldown of 0 runs it once each time the check fails.
* **Schedule a script**: run the script on the device later, either after a delay or at the next
  time of day in a time zone, such as a clean-up at 02:00 out of hours. One run is scheduled each
  time the check starts failing; if the check passes again before then, the run is dropped. The
  asset page shows when a scheduled run is waiting.
* **Create a ticket**: raise a ticket for the device's company with the device linked. While that
  ticket is not closed, a later failure on the same device adds an internal update to it instead of
  raising another, and the ticket gets an internal update when the check passes again.

Script runs started by checks show as **Check** in run history.

## Machine stats and the stats database

While a device has disk space, ping, CPU, memory or network checks, MyPortal keeps the values its
agent reports so technicians can see them on the asset page (**Machine stats**, last 24 hours, 7
days or 30 days): CPU and memory use, free space on each drive, network throughput and ping times.
Stats are kept in their **own database**, separate from the main and event log databases, and are
left out of backups unless you ask for them.

Add these to the environment file (`/etc/myportal.env` on bare metal, `myportal.env` on Docker)
and restart MyPortal:

| Variable | Default | Description |
|----------|---------|-------------|
| `STATS_DB_HOST` | empty | MySQL or MariaDB host for device stats |
| `STATS_DB_PORT` | `3306` | Port |
| `STATS_DB_USER` | empty | User |
| `STATS_DB_PASSWORD` | empty | Password |
| `STATS_DB_NAME` | empty | Database name (letters, digits, `_` and `$`) |
| `STATS_DB_BACKUP` | `false` | `true` also backs up the stats database (see [Backups](Backups.md)) |
| `STATS_RETENTION_DAYS` | `90` | Days of stats to keep; a daily job (03:45) deletes older ones. `0` keeps everything |
| `STATS_SAMPLE_MINUTES` | `5` | Minutes between the samples kept for each device (1 to 60) |

Without `STATS_DB_HOST`, `STATS_DB_USER` or `STATS_DB_NAME`, checks still run but no history is
kept, and nothing is written to the main database instead. On startup MyPortal connects, creates the
database if it is missing (when the user is allowed to) and creates any missing tables; if it cannot
connect it still starts and tries again every minute. For example:

```sql
CREATE DATABASE myportal_stats CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'myportal_stats'@'%' IDENTIFIED BY 'a-strong-password';
GRANT CREATE, ALTER, INDEX, SELECT, INSERT, DELETE ON myportal_stats.* TO 'myportal_stats'@'%';
```

The agent reports every minute and MyPortal keeps the first value in each sample period, so with
the default of 5 minutes each device adds about 12 rows an hour for each value it reports.

## Agent API

* `GET /api/rmm/agent/checks/config` tells the agent what to measure: CPU, memory, disks and
  network (true or false), the ping targets and the services with their expected state and fix.
* `POST /api/rmm/agent/checks/report` sends one round of measurements.

Both use the agent's bearer token, like the rest of the agent API.
