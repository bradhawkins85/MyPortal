# RMM Checks

Checks watch devices with the [RMM agent](RMM%20Scripts.md) and act when something goes wrong.
Manage them from **Checks** in the main menu (`/rmm/checks`); each asset's page shows the checks
that reach it under **Checks**, with their latest results, and charts of the device's stats under
**Machine stats**. On the Checks page, select a check's **failing** or **with a problem** count to
see which of the company's devices it is on, with each device's latest value, when the problem
started and when it was last checked.

## Types of check

| Check | Fails when | Measured by |
|-------|------------|-------------|
| **Disk space** | A drive's free space is below (or above) a number of GB or a percentage. Use a drive letter such as `C:`, a mount point such as `/var`, or `*` for every fixed drive. | Agent |
| **Ping** | Pinging a host or IP address loses at least the chosen share of its four packets (100% by default, so only when nothing answers), or the average reply takes longer than the chosen time. | Agent |
| **CPU load** | CPU use is over a percentage, for example over 80% for 30 minutes. | Agent |
| **Free memory** | Free memory is below a number of GB or a percentage. | Agent |
| **Windows service** | A service is not running (or not stopped). On Linux the name is a systemd unit. | Agent |
| **Network throughput** | Data received, sent or both, on one interface or all of them together, is above (or below) a number of Mbps. | Agent |
| **File or folder** | A path does not exist (or exists when it should not). Use a full path such as `C:\Program Files\App\app.exe`, `\\server\share\file.txt` or `/etc/app.conf`. `*` and `?` match any name, for example `C:\Users\*\Desktop\App.lnk`, and `%NAME%` is an environment variable on the device, such as `%ProgramData%`. | Agent |
| **Process** | No process with a name is running (or one is running when it should not be). Use the program's file name, such as `notepad.exe` or `nginx`; case and `.exe` do not matter. | Agent |
| **Program check** | A program is missing (or installed when it should not be), or an installed version fails an optional numeric comparison. Use its exact installed software name, ignoring case. | Agent |
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

On Windows the agent opens a service with only the access each step needs (querying its status,
or starting or stopping it). Some protected services, such as Microsoft Defender, refuse even a
status query; for those the agent reads the state from the Service Control Manager's list of
services instead. A check reports **Unknown** only when neither way works.

CPU, memory, disk and network are measured on Windows, Linux and macOS; ping, file and process
checks on all three; service checks on Windows and Linux. On macOS, disk checks leave out mounted
disk images (such as an opened `.dmg` installer) and other read-only volumes under `/Volumes`.

The agent runs as System (root on Linux and macOS), so a file check's `%USERPROFILE%` is the system
profile, not the signed-in user's; use a pattern such as `C:\Users\*\...` instead. A path the agent
cannot read counts as a problem, with the reason as the result. A process check matches the name
Windows shows in Task Manager's Details tab; on Linux it matches either the process's short name or
its program's file name, and on macOS the program's file name.

A **Program check** reads installed software directly on each measurement, independently of
whether the program is running. Windows checks include 32-bit and 64-bit machine installations
and loaded user hives; Linux uses dpkg, RPM or pacman. Other platforms report **Unknown**.
Use the Windows Apps & features name (such as `Google Chrome`) or the Linux package name.
The optional version requirement supports equals, does not equal, less than, at most,
greater than and at least. Numeric components are compared (`1.10` is newer than `1.9`;
`1.2` equals `1.2.0`), and every installed version must satisfy the requirement.
Unreadable versions or collection errors report **Unknown**, distinct from **Not installed**.
Program checks require an updated RMM agent; older agents leave them waiting for measurements.

## Who a check applies to

Like [event log rules](RMM%20Event%20Logs.md#event-log-rules), a check applies to **every company**
(super admins only), **every device in a company**, or **one device**. When checks at different
levels watch the same thing (the same type and drive, ping target, service, interface, path,
process or script),
the most specific level wins: a device's own check replaces its company's, which replaces one for
every company. A device's own check that is **off** switches the thing off for that device. On an
asset page, **Change for this device** on a company or every-company check copies it as the
device's own, ready to change or turn off. Several checks at the same level all run, for example a
ticket at 15% free space and a clean-up script at 5%.

### Advanced device rules

The company/device selection sets the scope boundary. **Devices matching advanced
rules** narrows that scope using company and inventory conditions. Each group can
match **all (AND)** or **any (OR)** of its conditions and child groups; use **Add
group** to combine several groups or nest them. Groups support up to eight levels
and 250 total conditions/groups. Existing checks retain their unrestricted scope
until you choose advanced rules.

Examples:

* **VIP company servers**: an AND group with asset catalogue type equal to `server`
  and company VIP equal to `true`.
* **WireGuard with a custom field**: an AND group with installed software containing
  `WireGuard` and the asset custom field `VPNEnabled` equal to `true`.
* **Either device population**: put those two AND groups inside an OR group.
* **Large customer Windows fleet**: company active agent count at least `25`, a
  company variable `ServiceTier` equal to `Premium`, RAM at least `16` GB, and
  recorded asset IP in `10.50.0.0/16`, all joined with AND.

Fields cover company identity, VIP status, tags and variables; asset identity,
catalogue type, tags and custom fields; hardware manufacturer/model, CPU and core
counts, RAM and storage; OS name/version/build; agent architecture/version and
check-in IP; recorded device IPs; and installed software names, versions and
publishers. Software version/publisher conditions specify the software name so
they compare the same installed package. Custom fields and company variables
accept definition names or IDs (numeric keys refer to definition IDs).

Text comparisons ignore case. Numeric conditions support comparisons and
inclusive ranges, versions compare numeric components, and subnet matching
accepts IPv4 or IPv6 CIDRs. Negative collection conditions require no known item
to match. Unknown inventory fails comparisons, including negative comparisons;
**Is not set** explicitly selects missing values. A confirmed empty collection
can match a negative condition. Invalid or empty rule groups cannot be saved, and
malformed stored rules select no devices.

**Preview matching devices** shows current matching device/company names without
running checks or exposing inventory or variable values. Only super admins can
preview or save rules across every company. MyPortal evaluates the rules when it
builds agent configuration, handles reports, evaluates offline/script checks, and
runs delayed actions. Inventory changes can add or remove devices automatically;
the agent receives refreshed configuration within its normal five-minute refresh
interval. Software/hardware matching uses the latest successfully reported
[device inventory](RMM%20Inventory.md), so an inventory refresh may be needed after
installing software or changing hardware.

Rules are evaluated **before** scope overrides: a company or device check only
replaces a broader check on devices its rules match. This includes disabled
overrides. A pending script action is dropped if the check no longer applies.

## What happens when a check fails

Checks offer these failure actions:

* **Show the result only**: the result shows on the asset page and the Checks page.
* **Run a script**: queue a Gitea script on the device. Its values can use
  `{{check.name}}`, `{{check.type}}`, `{{check.target}}`, `{{check.result}}` and
  `{{check.device}}` as well as the usual variables. While the check keeps failing the script runs
  again every **cooldown** minutes; a cooldown of 0 runs it once each time the check fails.
* **Schedule a script**: run the script on the device later, either after a delay or at the next
  time of day in a time zone, such as a clean-up at 02:00 out of hours. One run is scheduled each
  time the check starts failing; if the check passes again before then, the run is dropped. The
  asset page shows when a scheduled run is waiting.
* **Install a Chocolatey package**: enter an exact package ID, such as `googlechrome`.
  Active Windows devices install it through the company's enabled Chocolatey sources,
  installing Chocolatey if needed. Jobs appear in patch run history and never automatically
  reboot. While the check keeps failing, the action repeats after its cooldown; 0 attempts it
  once per failure. A busy patch queue or unavailable source is recorded in the check summary.
  Use **Only act on these results** with `Not installed` to avoid installation on an Unknown
  result. Already installed Chocolatey packages are skipped.
* **Create a ticket**: raise a ticket for the device's company with the device linked. While that
  ticket is not closed, a later failure on the same device adds an internal update to it instead of
  raising another, and the ticket gets an internal update when the check passes again.
  Enable **Auto-close when resolved** to also move the ticket to **Resolved** when that status
  exists, or **Closed** otherwise. This option is off by default. With it enabled, a later
  failure raises a new ticket, and tickets already resolved or closed are left in that state.

Script runs started by checks show as **Check** in run history.

## Machine stats and the stats database

While a device has disk space, ping, CPU, memory or network checks, MyPortal keeps the values its
agent reports so technicians can see them on the asset page (**Machine stats**, last 24 hours, 7
days or 30 days): CPU and memory use, free space on each drive, network throughput and ping times.
Temperature sensors also appear as graphs in Machine stats. The RMM agent collects them at
startup and every detailed check-in (approximately 15 minutes), independently of enabled checks.
Each sensor has its own graph in Celsius, using the same ranges and retention settings. Missing
readings are never stored as zero-temperature samples; extended interruptions produce gaps.
Windows assets do not show temperatures. Earlier Windows agents installed the PawnIO driver
to read them, and machines running those agents crashed with stop code 0x7E. Current Windows agents
remove PawnIO at service startup, even if other software installed it, and MyPortal ignores
temperatures that older Windows agents still send.
Linux uses sysfs sensors; macOS temperature collection is currently unsupported.

Use **Stat type** to focus on CPU, memory, disk, network, ping or temperature charts.
Use **Temperature device** to limit temperature charts to a reported hardware component,
such as the CPU or a particular SSD. Other stat types remain visible when **All stat types**
is selected. Click **Filter** to apply; the selected filters stay active when changing time
ranges. **Clear** restores all stat types and temperature devices while keeping the time range.
If no readings match, the page shows an empty result and keeps the filters available.

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
  network (true or false), the ping targets, the services with their expected state and fix, the
  file paths and the process names.
* `POST /api/rmm/agent/checks/report` sends one round of measurements.

Both use the agent's bearer token, like the rest of the agent API.
