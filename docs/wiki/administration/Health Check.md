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

Every check is listed under **Health checks** in the main menu (`/health-checks`). Users with
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
| Updates and restarts | Pending restarts, the last installed update, failed updates and uptime |
| Security | Defender (real-time protection, signature and scan age, tamper protection), other antivirus, firewall profiles, BitLocker, Secure Boot and TPM |
| Battery, network, power | Battery wear (full charge against design capacity) and cycles, network adapter errors, the power plan and fast startup |
| OS integrity | `DISM /Online /Cleanup-Image /CheckHealth` (component store healthy, repairable or not repairable) |

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
   errors, heavy memory pressure, pending restarts, old updates, real-time protection off or
   a worn battery. They set the **health score** (100, less 20 per critical, 7 per warning and
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
Actions entry and section, and the routes go away, and the troubleshooter keeps recording the
logs it could not read.
