# Health Check

The **Health check** is an AI review of one computer's health. It runs through the
computer's [tray app](../getting-started/Tray%20App.md), uses the AI troubleshooter's
settings (the same language model, public web search, internal knowledge base and approved
log list), and lists the steps that bring the computer back to optimal performance.

The health check is an **RMM option**: it is only offered on assets with the
[RMM agent](RMM%20Scripts.md) deployed (and the RMM feature pack turned on). Start one from the
asset page's **Actions** menu (**Run health check**, next to **Check in now**) or from its
**Health check** section, which also lets you leave PawnIO out (below). Running one needs the
same RMM scripts write access as the other RMM actions; the check itself runs through the
asset's tray app, which must be connected.

The tray app runs health checks from version **0.9.10**. Started on a computer with an older
tray app, the check waits (**Waiting for the device**) and MyPortal asks the tray app to update
itself; the check goes out as soon as the updated tray app reports its new version, so you do
not need to start it again. A check still waiting after 24 hours fails like any other check
the device never picks up.

Computers in a [monthly check group](#monthly-health-checks) also run one automatically each
month. Every check is listed under **Health checks** in the main menu (`/health-checks`), with
**Monthly check** on the ones the schedule started. Users with
RMM scripts access see their company's checks; helpdesk technicians and super admins see every
company's.

## What it looks at

On Windows the tray service runs a fixed, read-only PowerShell script as SYSTEM that reads
built-in Windows metrics only (CIM/WMI classes, the registry, event logs, `DISM /CheckHealth`
and the volume fragmentation analysis). Nothing on the computer is changed.

| Area | What is read |
| --- | --- |
| Drives and impending failure | Each physical disk's health and operational status (Predictive Failure, Degraded), its reliability counters (temperature, wear, power-on hours, read/write errors corrected and uncorrected, latency) and the SMART failure prediction (`MSStorageDriver_FailurePredictStatus`); disk, storport and NTFS errors in the System log |
| chkdsk | Each volume's dirty bit, whether a check is scheduled for the next restart (`BootExecute`) and the latest chkdsk results (Wininit / Chkdsk events) |
| Defrag and TRIM | The ScheduledDefrag task (on, last run, result), whether TRIM is on, the last optimisation events, and the fragmentation of hard disks (`DefragAnalysis`, read-only, stopped after 3 minutes) |
| Thermals | Thermal zones and how far they throttle the CPU, CPU performance limit and frequency, thermal events, drive temperatures and, on physical hardware, hardware sensors (below) |
| Performance | CPU load, DPC/interrupt time, processor queue, memory and commit use, paging, disk busy time, the slowest recent boots and what slowed them (Diagnostics-Performance), the busiest processes, startup items and the Reliability Monitor stability index |
| Stability and hardware errors | Blue screens, unexpected shutdowns, WHEA hardware errors, application crashes and hangs (and the apps crashing most), service crashes and Windows Memory Diagnostic results |
| Devices and drivers | Devices with a driver or hardware problem and the versions of display, network, storage and Bluetooth drivers |
| Services | Automatic services that are not running and the exit code they stopped with |
| Updates and restarts | Pending restarts, the last installed update, failed updates, uptime and whether the Windows Update service is disabled |
| Security | Defender (real-time protection, signature and scan age, tamper protection), other antivirus, firewall profiles, BitLocker, Secure Boot, TPM, User Account Control, whether the SMB server accepts SMBv1, and whether Remote Desktop is on and requires Network Level Authentication |
| Windows version, activation and clock | The Windows edition, build and version (checked against Microsoft's end-of-support dates for Home/Pro and Enterprise/Education), the activation status, and the Windows Time service with how often it logged that the clock went a day without synchronising |
| Battery, network, power | Battery wear (full charge against design capacity) and cycles, network adapter errors, the power plan and fast startup |
| OS integrity | `DISM /Online /Cleanup-Image /CheckHealth` (component store healthy, repairable or not repairable) |

The UAC, SMBv1, Remote Desktop, activation and clock readings come from tray app builds
released after the checks were added; older tray apps leave them out and those checks stay
quiet.

macOS sends a smaller snapshot from `system_profiler`, `pmset`, `sysctl` and
`memory_pressure`: drive SMART status, volume free space, battery condition and cycles,
memory pressure, CPU speed limit and recent kernel panics.

The device also sends these logs from the last 72 hours (redacted like the troubleshooter's):
System, Application, Diagnostics-Performance, Kernel-Power thermal, NTFS, Storport,
Resource-Exhaustion-Detector and WHEA errors (macOS: kernel, power and install logs and
panic reports).

## Hardware sensors and PawnIO

CPU, GPU and motherboard temperatures, fan speeds, voltages and clocks are read with the
LibreHardwareMonitor library built into the Windows tray service. Its CPU and motherboard
sensors need the PawnIO driver, so **on physical hardware only** (never on a virtual
machine) and only when allowed, the tray service:

1. records in its state folder that it is about to install PawnIO,
2. installs the bundled, pinned PawnIO 2.1.0 (SHA-256 checked) silently,
3. reads the sensors in a separate PowerShell process,
4. uninstalls PawnIO, confirms it is gone and removes the record — even when reading the
   sensors fails or the check runs out of time.

If the service stops part-way, it removes PawnIO the next time it starts. A PawnIO that was
already installed before the check is used and left as it was. The report says which of
these happened, and warns when removal failed.

PawnIO is the driver earlier RMM agents kept installed, on machines that then reported
`SYSTEM_THREAD_EXCEPTION_NOT_HANDLED` (0x7E) crashes; the RMM agent still removes it when it
starts. The health check only installs it for the minutes the sensors take to read. Turn it
off for every check with `HEALTH_CHECK_PAWNIO_ENABLED=false`, or untick **Read hardware
sensors** when starting a check from the Health check section (the Actions menu follows the
setting); built-in Windows sensors are still read. Development tray
builds without the sensor bundle report that sensors are not part of the build.

## How the report is written

1. **Automatic checks** — fixed rules flag problems in the snapshot without the AI model:
   for example a drive under 5% free, a dirty volume, a failing or worn drive, uncorrected
   read errors, a SMART failure prediction, overheating or throttling, blue screens, WHEA
   errors, failed memory tests, heavy memory pressure, no page file, stopped automatic
   services, pending restarts, old updates or a disabled Windows Update service, a Windows
   release out of support or not activated, a clock that does not synchronise, real-time
   protection or tamper protection off, no recent antivirus scan, BitLocker off on the system
   drive, Secure Boot or the TPM off, UAC off, SMBv1 on, Remote Desktop without NLA, network
   adapter errors or a worn battery. They set the **health score** (100, less 20 per critical, 7 per warning and
   1 per informational flag) and the overall status.
2. **Triage** — the AI model reviews the snapshot, the flags and the logs, picks search terms
   and may ask the device for more logs from the approved list, which are collected straight
   away.
3. **Report** — with any public web pages (`TROUBLESHOOT_WEB_SEARCH_*`) and internal
   knowledge base articles found for the issues, the model writes a summary, a status for
   each area, findings with evidence and the ordered **steps to optimal performance**. The
   overall status is the worse of the rules' and the model's.

If the model cannot be reached the report still shows the automatic checks. Checks whose
device never picks them up fail after 24 hours; a check waiting for extra logs writes its
report with what it has after 2 hours. Started through the API with a `ticket_id`, the
progress and the finished report are also posted to that ticket as internal notes.

LLM requests are listed as **Health check** on the [LLM Usage](LLM%20Usage.md) page.

## Monthly health checks

Open **Monthly checks** from the Health checks page's Actions menu (`/health-checks/monthly`).
A computer with the RMM agent gets one health check a month when it matches **any** group
that is on. Each group has:

* **Applies to**: the company you are working in, or every company (super admins only).
* **Which computers**: every computer in scope, or computers matching **device rules** —
  the same nested AND/OR rule builder, fields and operators as
  [Checks](RMM%20Checks.md#advanced-device-rules) (company VIP status, tags, variables and
  custom fields, operating system name and version, hardware, installed software, IP
  addresses and more), with **Preview matching devices**. For example, *VIP workstations* is
  `Company VIP status is Yes` AND `Asset operating system does not contain Server`, and *Windows 11 PCs*
  is `Asset operating system contains Windows 11`. Add several groups to check several sets of
  computers; a computer in more than one group still gets one check a month.

The **All computers** group (every company, every computer) is created on upgrade so every
computer keeps being checked; narrow it, turn it off or delete it to check only the groups you
build. Company admins with RMM scripts write access manage their own company's groups; groups
for every company need a super admin.

Each computer in a group gets a random day and time in the month (UTC): the first within 30
days of joining, then one in each following month, so checks spread out instead of starting
together. When it is due:

* with the tray app online, an ordinary health check starts (with `HEALTH_CHECK_PAWNIO_ENABLED`
  deciding PawnIO) and the next month's time is picked;
* with the tray app offline, it is tried again every 30 minutes until the month ends, then
  moves on to the next month;
* a month in which the computer already had a completed health check (a technician ran one)
  is skipped, and one with a check running waits an hour.

The page lists the company's upcoming checks and what happened last month, and the asset
page's Health check section shows when its next check is due. Computers are picked up within
ten minutes of matching a group; saving or deleting a group drops the schedules of computers
no group matches any more. Device rules use the latest inventory, and are checked again just
before each health check starts. Nothing runs while no language model is configured, and
`HEALTH_CHECK_MONTHLY_ENABLED=false` turns monthly checks off (groups are kept).

## Watching for problems in the event logs

A health check finds problems when it runs; [event log rules](RMM%20Event%20Logs.md#event-log-rules)
flag them as they happen. On Windows, a completed report's **Watch for these problems** section
lists event log rules for what it found, each with the Windows events it matches, why they
matter and **Create this rule**, which opens the event log rule form filled in (raise a ticket,
for every device in the company with this computer preselected should you narrow it) for you to
review and save. The suggestions are:

* MyPortal's own, for each automatic check that has one: for example `disk` events 7, 11, 51,
  52, 153 and 154 before a drive fails, storage controller resets (129), NTFS corruption (55,
  98), Kernel-Power 41 and EventLog 6008 unexpected shutdowns, blue screens (WER 1001), WHEA
  hardware errors, CPU firmware throttling (Kernel-Processor-Power 37), application crashes and
  hangs (1000, 1002), services stopping (7031, 7034), failed Windows updates
  (WindowsUpdateClient 20), low memory (2004), slow start-up (Diagnostics-Performance 101-109),
  and Microsoft Defender turned off (5001, 5010, 5012) or failing to update (2001, 2003);
* the AI model's, marked **AI suggestion**, for problems it found in the logs it read. They are
  checked like the rule form, but check the event IDs before relying on them.

A rule only matches events the devices collect: suggestions that read a log other than
Application and System say **Collect … first**; add the log under **Logs collected** on the
Event logs page. The Detections page lists every suggestion.

## Detections and fixes

**Detections and fixes** (`/health-checks/detections`, also in the main menu as **Health
detections**) lists everything the automatic checks can find, grouped by area, with its
severity, its event log rules and, where a script can fix it, a **suggested script** to start
from (for example `DISM /Online /Cleanup-Image /RestoreHealth` then `sfc /scannow` when the
component store is damaged but repairable, `Repair-Volume` for a dirty volume, `Update-MpSignature`
for old antivirus definitions or re-enabling the ScheduledDefrag task).

**Link a script** to a detection and it runs on the computer through its RMM agent when a
health check finds it, once the check has completed — manual and monthly checks alike. A link
applies to the company you are working in or, for super admins, to every company (scripts in
`Common/` only). A company's own link replaces the every-company one for its computers; link no
script to stop the every-company script running there. After you choose a script and save, its
parameters appear; besides the usual variables they can use `{{health.detection}}`,
`{{health.label}}`, `{{health.title}}` (what was found, for example `C: is almost full (3.5%
free)`), `{{health.detail}}`, `{{health.severity}}` and `{{health.check_id}}`.

A script runs at most once per health check, even when several of the detections it is linked
to are found. The report's **Automatic fixes** section, the ticket note (for checks started from
a ticket) and the asset's script run history (**Health check fix**) show what was queued.
Linking scripts needs RMM scripts write access; links and their removal are audited.

Tick **Create a ticket** on a link, with or without a script, to raise a ticket for the
computer's company, with the asset linked, for a technician to investigate when a health check
finds the detection. The ticket lists what was found and links to the report. While it is open,
later health checks that find the detection on that asset add an internal update to it instead
of raising another; once it is closed the next one raises a new ticket. Tickets raised show in
the report's **Automatic fixes** section too.

Each detection's **Assets** button shows how many of the company's assets report it in their
latest completed health check, and opens a list of them with what was found, its severity and a
link to that health check's report.

## Required logs

The AI troubleshooter and the health check only read event logs and log files on the
approved list. Whenever either asks for a log it cannot have, the log is listed on
**Admin → Required logs** (`/admin/required-logs`, super admins):

* **Not on the approved list** — the model asked for an event log channel or a log file
  outside the approved folders. It was not collected. **Approve** adds the channel, or the
  folder holding the file (you can change the suggested folder), to the approved list for
  every later run; the tray app re-checks each entry before reading. A folder must name at
  least two real folders, and only log-type files (`.log`, `.txt`, `.wer`, `.lo_`, `.ips`,
  `.crash`, `.panic`, `.diag`) are read from it.
* **Unavailable on the endpoint** — the log is allowed but the computer could not read it:
  the channel is off or missing (turn it on with `wevtutil sl "<channel>" /e:true`, for
  example from an RMM script), no matching file exists, or an older tray app skipped it.

Dismiss anything you do not want read; a dismissed log that is asked for again is marked
**Requested again since**. A log asked for again after approval reopens, because the
approval did not cover it. Approvals, dismissals and reopenings are audited.

The approved list is the built-in channels and folders, plus
`TROUBLESHOOT_EVENT_LOGS_WINDOWS` (extra event log channels, separated by `;`) and
`TROUBLESHOOT_LOG_FOLDERS_WINDOWS` / `_MACOS`, plus everything approved on this page.

## APIs

| Endpoint | Method | Auth | Purpose |
| --- | --- | --- | --- |
| `/rmm/assets/{asset_id}/health-check` | POST (form) | RMM scripts write | The Actions menu and Health check section; optional `pawnio_choice` and `allow_pawnio` fields |
| `/api/assets/{asset_id}/health-checks` | POST | Helpdesk / super admin | Start a check; body `{"allow_pawnio": bool, "model": str, "ticket_id": int}` (all optional) |
| `/api/health-checks` | GET | Helpdesk / super admin | Recent checks (`?asset_id=`) |
| `/api/health-checks/{id}` | GET | Helpdesk / super admin | A check with its snapshot and report |
| `/api/health-checks/detections` | GET | Helpdesk / super admin | Every detection with its event log rules and linked scripts (`?company_id=` adds that company's links and the assets reporting each detection) |
| `/api/health-checks/detections/{key}/remediation` | PUT / DELETE | Helpdesk / super admin | Link a script and/or a ticket: `{"company_id": int, "script_id": int, "create_ticket": bool, "timeout_seconds": int, "is_enabled": bool, "values": {"param:Name": str}}` (no `company_id` for every company, super admins); `DELETE ?company_id=` removes it |
| `/api/health-checks/monthly/groups` | GET / POST | Helpdesk / super admin | Monthly check groups (`?company_id=`); create one with `{"name": str, "company_id": int, "target_rules": {...}, "is_enabled": bool}` |
| `/api/health-checks/monthly/groups/{id}` | PUT / DELETE | Helpdesk / super admin | Change or delete a group |
| `/rmm/event-logs/rules?new=1&suggest={monitor}` | GET | RMM scripts write | The event log rule form filled in from a suggestion (`&health_check={id}&monitor={n}` for one a report suggested) |
| `/api/health-checks/{id}/device-status` | POST | Device bearer | Progress from the tray app |
| `/api/health-checks/{id}/device-result` | POST | Device bearer | The snapshot (`snapshot` file) and log bundle (`log_bundle` file), multipart |
| `/api/required-logs` | GET | Super admin | Logs the AI agents asked for but could not read (`?view=open|approved|dismissed|all`) |
| `/api/required-logs/{id}` | PATCH | Super admin | `{"action": "approve"|"dismiss"|"reopen", "approve_value": str, "note": str}` |

The tray app receives `{"type": "health_check", "command_id", "health_check_id", "phase":
"snapshot"|"logs", "log_requests", "extra_log_folders", "extra_event_logs", "allow_pawnio",
"event_days"}` over its WebSocket; offline devices receive it from the queue when they
reconnect.

Every way of starting a check refuses assets without a deployed RMM agent. Disable the feature
with `DISABLED_FEATURE_PACKS=health_check` (or disable `rmm`): the menu entries, the asset page's
Actions entry and section, the monthly checks, the linked scripts and the routes go away, and the troubleshooter keeps recording the
logs it could not read.
