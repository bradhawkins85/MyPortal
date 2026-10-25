"""What the health check's automatic checks can find, how to watch for it and how to fix it.

Each fixed rule in :mod:`app.services.ai_health_check` reports a detection by
its key in :data:`DETECTIONS`. A detection carries:

* event log monitors (:data:`MONITORS`): event log rules
  (:mod:`app.services.rmm_event_log_rules`) that raise a ticket as soon as
  Windows logs the events that come with the problem, so it is flagged when
  it happens rather than at the next health check;
* a suggested script (``remedy``) when a script can fix the problem. Admins
  link a script to a detection on the Detections page, and it runs on the
  device when a health check finds it
  (:mod:`app.services.health_check_remediation`).

The report also keeps monitors the language model suggests from the logs it
read (:func:`parse_model_monitors`), checked like the event log rule form.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services import ai_troubleshooter
from app.services import rmm_event_log_rules
from app.services import rmm_event_logs

MAX_MODEL_MONITORS = 6
MAX_EVENT_IDS = 10


def _monitor(name: str, conditions: Sequence[tuple[str, str, str]], why: str) -> dict[str, Any]:
    log = next(value for field, _operator, value in conditions if field == "log_name")
    return {
        "name": name,
        "log": log,
        "conditions": [{"field": field, "operator": operator, "value": value} for field, operator, value in conditions],
        "why": why,
    }


# Event log rules worth creating for the problems the automatic checks find.
# Every monitor starts with the log it reads; logs other than Application and
# System must be added to the logs the devices collect first.
MONITORS: dict[str, dict[str, Any]] = {
    "disk_errors": _monitor(
        "Disk errors before a drive fails",
        [("log_name", "is", "System"), ("provider", "is", "disk"), ("event_id", "one_of", "7, 11, 51, 52, 153, 154")],
        "The disk driver logs bad blocks (7), controller errors (11), errors during paging (51), a drive predicting "
        "its own failure (52) and retried or failed I/O (153, 154), usually well before the drive stops working.",
    ),
    "storage_resets": _monitor(
        "Storage controller resets",
        [("log_name", "is", "System"), ("event_id", "is", "129"), ("message", "contains", "Reset to device")],
        "The storage driver (storahci, stornvme, iaStor…) logs 129 when a request times out and it resets the "
        "drive. Repeated resets cause freezes and come before drive, cable or controller failure.",
    ),
    "ntfs_corruption": _monitor(
        "File system corruption",
        [("log_name", "is", "System"), ("provider", "one_of", "Ntfs, Microsoft-Windows-Ntfs"),
         ("event_id", "one_of", "55, 98"), ("level", "at_least", "Warning")],
        "NTFS logs 55 when it finds corruption in the file system structure, and 98 as a warning or error when a "
        "volume needs chkdsk /f to repair it.",
    ),
    "chkdsk_results": _monitor(
        "chkdsk results",
        [("log_name", "is", "Application"), ("provider", "one_of", "Microsoft-Windows-Wininit, Chkdsk"),
         ("event_id", "one_of", "1001, 26226")],
        "Wininit 1001 (chkdsk at start-up) and Chkdsk 26226 (online scans) record what chkdsk found and fixed, so "
        "corruption that keeps coming back is followed up.",
    ),
    "low_disk_space": _monitor(
        "A drive is nearly full",
        [("log_name", "is", "System"), ("provider", "is", "srv"), ("event_id", "is", "2013")],
        "Windows logs 2013 when a disk is at or near capacity, before apps and updates start failing.",
    ),
    "unexpected_shutdown": _monitor(
        "Unexpected shutdowns and power losses",
        [("log_name", "is", "System"), ("provider", "one_of", "Microsoft-Windows-Kernel-Power, EventLog"),
         ("event_id", "one_of", "41, 6008")],
        "Kernel-Power 41 and EventLog 6008 are logged on the next start after the computer stopped without shutting "
        "down cleanly: a crash, a hang that needed the power button, a power cut or a failing power supply.",
    ),
    "bugcheck": _monitor(
        "Blue screen crashes",
        [("log_name", "is", "System"), ("provider", "is", "Microsoft-Windows-WER-SystemErrorReporting"),
         ("event_id", "is", "1001")],
        "Logged after the computer restarts from a blue screen, with the stop code and where the memory dump was "
        "saved.",
    ),
    "whea": _monitor(
        "Hardware (WHEA) errors",
        [("log_name", "is", "System"), ("provider", "is", "Microsoft-Windows-WHEA-Logger"),
         ("level", "at_least", "Warning")],
        "The WHEA logger reports corrected and fatal errors from the CPU, memory and PCIe devices. Corrected errors "
        "that keep coming often turn into crashes.",
    ),
    "cpu_firmware_throttle": _monitor(
        "CPU speed limited by firmware",
        [("log_name", "is", "System"), ("provider", "is", "Microsoft-Windows-Kernel-Processor-Power"),
         ("event_id", "is", "37")],
        "Event 37 says system firmware is limiting the processor's speed, usually because it is too hot or the "
        "power supply cannot keep up.",
    ),
    "thermal_events": _monitor(
        "Overheating and thermal throttling",
        [("log_name", "is", "Microsoft-Windows-Kernel-Power/Thermal-Operational")],
        "Windows logs to this channel when a thermal zone passes a trip point and the computer slows itself down "
        "to cool off.",
    ),
    "display_driver": _monitor(
        "Display driver stopped responding",
        [("log_name", "is", "System"), ("provider", "is", "Display"), ("event_id", "is", "4101")],
        "Display 4101 means the graphics driver stopped responding and Windows reset it. Repeats point at the "
        "driver, overheating or the graphics card.",
    ),
    "driver_failures": _monitor(
        "Device drivers failing to load",
        [("log_name", "is", "System"), ("provider", "is", "Microsoft-Windows-Kernel-PnP"), ("event_id", "is", "219")],
        "Kernel-PnP 219 is logged when the driver for a device fails to load, naming the device.",
    ),
    "app_crashes": _monitor(
        "Application crashes and hangs",
        [("log_name", "is", "Application"), ("provider", "one_of", "Application Error, Application Hang"),
         ("event_id", "one_of", "1000, 1002")],
        "Application Error 1000 is logged when a program crashes and Application Hang 1002 when Windows closes one "
        "that stopped responding; the message names the program and the faulting module.",
    ),
    "service_crashes": _monitor(
        "Services stopping unexpectedly",
        [("log_name", "is", "System"), ("provider", "is", "Service Control Manager"),
         ("event_id", "one_of", "7031, 7034")],
        "The Service Control Manager logs 7031 and 7034 when a service terminates unexpectedly.",
    ),
    "low_memory": _monitor(
        "Low memory",
        [("log_name", "is", "System"), ("provider", "is", "Microsoft-Windows-Resource-Exhaustion-Detector"),
         ("event_id", "is", "2004")],
        "Windows logs 2004 when it runs low on virtual memory, naming the processes using the most.",
    ),
    "slow_boot": _monitor(
        "Slow start-up",
        [("log_name", "is", "Microsoft-Windows-Diagnostics-Performance/Operational"),
         ("event_id", "one_of", "101, 102, 103, 106, 109")],
        "Windows logs these when an application (101), driver (102), service (103), background optimisation (106) "
        "or device (109) made start-up slower than usual, naming the cause.",
    ),
    "update_failures": _monitor(
        "Windows Update install failures",
        [("log_name", "is", "System"), ("provider", "is", "Microsoft-Windows-WindowsUpdateClient"),
         ("event_id", "is", "20")],
        "WindowsUpdateClient 20 is logged each time an update fails to install, with the update and the error code.",
    ),
    "defender_disabled": _monitor(
        "Antivirus protection turned off",
        [("log_name", "is", "Microsoft-Windows-Windows Defender/Operational"), ("event_id", "one_of", "5001, 5010, 5012")],
        "Microsoft Defender logs 5001 when real-time protection is turned off, and 5010 and 5012 when scanning for "
        "spyware or viruses is turned off.",
    ),
    "defender_update_failed": _monitor(
        "Antivirus definitions failing to update",
        [("log_name", "is", "Microsoft-Windows-Windows Defender/Operational"), ("event_id", "one_of", "2001, 2003")],
        "Microsoft Defender logs 2001 when a security intelligence update fails and 2003 when an engine update "
        "fails.",
    ),
    "firewall_changed": _monitor(
        "Firewall settings changed",
        [("log_name", "is", "Microsoft-Windows-Windows Firewall With Advanced Security/Firewall"),
         ("event_id", "is", "2003")],
        "Windows logs 2003 when a firewall profile setting changes, including a profile being turned off; the "
        "message names the setting and its new value.",
    ),
}


def _detection(
    area: str, label: str, severity: str, description: str, *, monitors: Sequence[str] = (), remedy: str = "",
    platforms: Sequence[str] = ("windows",),
) -> dict[str, Any]:
    return {"area": area, "label": label, "severity": severity, "description": description,
            "monitors": list(monitors), "remedy": remedy, "platforms": list(platforms)}


_BOTH = ("windows", "macos")
_FILE_SYSTEM_REPAIR = (
    "Scan the volume online with Repair-Volume -DriveLetter C -Scan, then fix what it found with "
    "Repair-Volume -DriveLetter C -SpotFix (on the system drive the fix runs at the next restart)."
)
_WINDOWS_UPDATE_RESET = (
    "Reset Windows Update: stop the wuauserv, bits and cryptsvc services, rename C:\\Windows\\SoftwareDistribution "
    "and C:\\Windows\\System32\\catroot2, start the services again and scan with UsoClient StartScan."
)
_RESTART = (
    "Restart out of hours, for example shutdown /r /t 600 /c \"Restarting to finish updates\", after warning the "
    "user."
)

# Every detection the fixed rules can report. Keys are stored with reports and
# remediation links, so never rename one.
DETECTIONS: dict[str, dict[str, Any]] = {
    "volume_almost_full": _detection(
        "storage", "A drive is almost full", "critical", "A volume has under 5% free space.",
        monitors=["low_disk_space"], platforms=_BOTH,
        remedy="Clear temporary and old update files, for example Remove-Item \"$env:windir\\Temp\\*\" -Recurse "
               "-Force -ErrorAction SilentlyContinue and DISM /Online /Cleanup-Image /StartComponentCleanup.",
    ),
    "volume_low_space": _detection(
        "storage", "A drive is low on space", "warning", "A volume has under 10% free, or the boot volume under 5 GB.",
        monitors=["low_disk_space"], platforms=_BOTH,
        remedy="Clear temporary and old update files, for example Remove-Item \"$env:windir\\Temp\\*\" -Recurse "
               "-Force -ErrorAction SilentlyContinue and DISM /Online /Cleanup-Image /StartComponentCleanup.",
    ),
    "volume_dirty": _detection(
        "chkdsk", "A volume is marked dirty", "warning",
        "Windows set the volume's dirty bit: the file system needs checking and chkdsk runs at the next restart.",
        monitors=["ntfs_corruption", "chkdsk_results"], remedy=_FILE_SYSTEM_REPAIR,
    ),
    "volume_unhealthy": _detection(
        "chkdsk", "Windows reports a volume as unhealthy", "critical",
        "The volume's health status is Warning or Unhealthy, usually because the file system has errors.",
        monitors=["ntfs_corruption", "chkdsk_results"], remedy=_FILE_SYSTEM_REPAIR,
    ),
    "disk_failing": _detection(
        "storage", "A drive is failing", "critical",
        "Windows reports the physical disk as Unhealthy, Predictive Failure, Error or Lost Communication.",
        monitors=["disk_errors", "storage_resets"],
    ),
    "disk_degraded": _detection(
        "storage", "Windows reports a problem with a drive", "warning",
        "The physical disk's health is Warning or its status is Degraded.",
        monitors=["disk_errors", "storage_resets"],
    ),
    "disk_smart_failing": _detection(
        "storage", "SMART reports a drive is failing", "critical", "The drive's own SMART self-assessment failed.",
        monitors=["disk_errors"], platforms=_BOTH,
    ),
    "disk_uncorrected_errors": _detection(
        "storage", "A drive has uncorrected read/write errors", "critical",
        "The drive could not read or write some data even after retrying.",
        monitors=["disk_errors", "storage_resets"],
    ),
    "disk_worn_out": _detection(
        "storage", "A drive is worn out", "critical", "An SSD has used 90% or more of its rated write endurance.",
        monitors=["disk_errors"],
    ),
    "disk_wearing": _detection(
        "storage", "A drive is wearing out", "warning", "An SSD has used 70% or more of its rated write endurance.",
        monitors=["disk_errors"],
    ),
    "disk_hot": _detection(
        "thermals", "A drive is running hot", "warning", "A hard disk is at 55 °C or more, or an SSD at 70 °C or more.",
    ),
    "disk_smart_prediction": _detection(
        "storage", "SMART predicts a drive will fail", "critical",
        "The drive's failure prediction (MSStorageDriver_FailurePredictStatus) is set.",
        monitors=["disk_errors"],
    ),
    "ntfs_errors": _detection(
        "chkdsk", "File system errors", "warning", "NTFS logged warnings or errors in the last week.",
        monitors=["ntfs_corruption", "chkdsk_results"], remedy=_FILE_SYSTEM_REPAIR,
    ),
    "disk_io_errors": _detection(
        "storage", "Disk I/O errors", "warning",
        "The disk or storage drivers logged errors in the last week (critical at 10 or more).",
        monitors=["disk_errors", "storage_resets"],
    ),
    "chkdsk_scheduled": _detection(
        "chkdsk", "A disk check is scheduled", "info", "chkdsk is set to run at the next restart.",
        monitors=["chkdsk_results"],
    ),
    "defrag_task_disabled": _detection(
        "defrag", "Scheduled drive optimisation is turned off", "warning",
        "The ScheduledDefrag task that trims SSDs and defragments hard disks is disabled.",
        remedy="Turn the task back on with Enable-ScheduledTask -TaskPath '\\Microsoft\\Windows\\Defrag\\' "
               "-TaskName 'ScheduledDefrag'.",
    ),
    "trim_disabled": _detection(
        "defrag", "TRIM is turned off", "warning", "Windows is not telling SSDs which blocks are free.",
        remedy="Turn TRIM back on with fsutil behavior set DisableDeleteNotify 0.",
    ),
    "volume_fragmented": _detection(
        "defrag", "A hard disk is fragmented", "warning", "Defrag analysis recommends defragmenting, or 10% or more is fragmented.",
        remedy="Defragment the hard disk out of hours with Optimize-Volume -DriveLetter D -Defrag (never on SSDs).",
    ),
    "thermal_zone_hot": _detection(
        "thermals", "A thermal zone is hot", "warning", "A built-in thermal zone is at 80 °C or more (critical at 90 °C).",
        monitors=["thermal_events", "cpu_firmware_throttle"],
    ),
    "thermal_throttling": _detection(
        "thermals", "A thermal zone is throttling the CPU", "warning", "A thermal zone is holding the CPU below full speed.",
        monitors=["thermal_events", "cpu_firmware_throttle"],
    ),
    "thermal_events": _detection(
        "thermals", "Thermal events", "warning", "Windows logged thermal events in the last week.",
        monitors=["thermal_events"],
    ),
    "cpu_speed_limited": _detection(
        "thermals", "macOS is limiting the CPU's speed", "warning",
        "The Mac is too hot or its power supply is limited.", platforms=("macos",),
    ),
    "cpu_temperature_high": _detection(
        "thermals", "CPU temperature is high", "warning", "A CPU sensor reads 85 °C or more (critical at 95 °C).",
        monitors=["thermal_events", "cpu_firmware_throttle", "unexpected_shutdown"],
    ),
    "gpu_temperature_high": _detection(
        "thermals", "GPU temperature is high", "warning", "A GPU sensor reads 85 °C or more (critical at 95 °C).",
        monitors=["display_driver"],
    ),
    "fan_stopped": _detection(
        "thermals", "A fan reads 0 RPM while the CPU is hot", "warning",
        "A fan sensor reads 0 RPM while the CPU is at 70 °C or more.",
        monitors=["thermal_events", "cpu_firmware_throttle"],
    ),
    "cpu_busy": _detection(
        "performance", "The CPU is busy", "warning", "CPU use is at 90% or more.",
    ),
    "cpu_performance_limited": _detection(
        "thermals", "CPU performance is limited", "warning", "Thermal or power limits hold the CPU below 90% of its performance.",
        monitors=["cpu_firmware_throttle"],
    ),
    "high_dpc_time": _detection(
        "drivers", "Drivers use a lot of CPU time", "warning",
        "Interrupts and DPCs use 10% or more of CPU time, which often means a faulty driver.",
        monitors=["driver_failures"],
    ),
    "cpu_queue_long": _detection(
        "performance", "Threads are waiting for the CPU", "warning", "The processor queue is longer than twice the number of logical processors.",
    ),
    "memory_commit_high": _detection(
        "memory", "Committed memory is near the limit", "warning", "Committed memory is at 90% or more of the limit (critical at 97%).",
        monitors=["low_memory"],
    ),
    "memory_used_high": _detection(
        "memory", "Memory is nearly all used", "warning", "Memory use is at 90% or more.",
        monitors=["low_memory"], platforms=_BOTH,
    ),
    "heavy_paging": _detection(
        "memory", "Heavy paging", "warning", "Windows is paging 1,000 or more pages a second.",
        monitors=["low_memory"],
    ),
    "low_memory_installed": _detection(
        "memory", "Little memory installed", "info", "The computer has under 8 GB of memory.",
    ),
    "slow_boot": _detection(
        "performance", "Slow start-up", "warning", "The last boot took 2 minutes or more.",
        monitors=["slow_boot"],
    ),
    "low_stability_index": _detection(
        "stability", "Low stability index", "warning", "Reliability Monitor's stability index is under 5 of 10.",
        monitors=["app_crashes", "unexpected_shutdown", "bugcheck"],
    ),
    "component_store_repairable": _detection(
        "os", "The Windows component store is damaged but repairable", "warning",
        "DISM /CheckHealth found damage it can repair. Updates and Windows features can fail until it is repaired.",
        monitors=["update_failures"],
        remedy="Repair it with DISM /Online /Cleanup-Image /RestoreHealth, then sfc /scannow (allow an hour; "
               "it needs Windows Update or a repair source).",
    ),
    "component_store_not_repairable": _detection(
        "os", "The Windows component store cannot be repaired", "critical",
        "DISM /CheckHealth found damage it cannot repair on its own.",
        monitors=["update_failures"],
        remedy="Point DISM at matching installation media, for example DISM /Online /Cleanup-Image /RestoreHealth "
               "/Source:wim:D:\\sources\\install.wim:1 /LimitAccess, or do an in-place repair install.",
    ),
    "blue_screens": _detection(
        "stability", "Blue screen crashes", "critical", "Windows crashed with a blue screen in the last 30 days.",
        monitors=["bugcheck", "whea"],
    ),
    "kernel_panics": _detection(
        "stability", "Kernel panics", "critical", "macOS restarted after a kernel panic in the last 30 days.",
        platforms=("macos",),
    ),
    "unexpected_shutdowns": _detection(
        "stability", "Unexpected shutdowns", "warning", "The computer stopped without shutting down cleanly in the last 30 days.",
        monitors=["unexpected_shutdown"],
    ),
    "whea_errors": _detection(
        "hardware", "Hardware (WHEA) errors", "critical", "The CPU, memory or PCIe devices reported errors in the last 30 days.",
        monitors=["whea"],
    ),
    "app_crashes": _detection(
        "stability", "Applications crash often", "warning", "Ten or more application crashes in the last 30 days.",
        monitors=["app_crashes"],
    ),
    "service_crashes": _detection(
        "services", "Services stop unexpectedly", "warning", "Services stopped unexpectedly five or more times in the last 30 days.",
        monitors=["service_crashes"],
    ),
    "device_problems": _detection(
        "drivers", "Devices with a driver or hardware problem", "warning", "Device Manager shows devices with a problem code.",
        monitors=["driver_failures"],
        remedy="Rescan for hardware with pnputil /scan-devices, then update or reinstall the problem devices' drivers.",
    ),
    "long_uptime": _detection(
        "updates", "Not restarted for a long time", "warning", "The computer has been up for 30 days or more.",
        remedy=_RESTART,
    ),
    "reboot_pending": _detection(
        "updates", "A restart is waiting", "warning", "A restart is needed to finish installing updates.",
        remedy=_RESTART,
    ),
    "updates_stale": _detection(
        "updates", "No recent updates", "warning", "No update has been installed for 60 days.",
        monitors=["update_failures"], remedy=_WINDOWS_UPDATE_RESET,
    ),
    "updates_failed": _detection(
        "updates", "Updates fail to install", "warning", "Windows Update installs failed in the last 30 days.",
        monitors=["update_failures"], remedy=_WINDOWS_UPDATE_RESET,
    ),
    "av_realtime_off": _detection(
        "security", "Real-time antivirus protection is off", "critical",
        "Microsoft Defender's real-time protection is off and no other antivirus is registered.",
        monitors=["defender_disabled"],
        remedy="Turn it back on with Set-MpPreference -DisableRealtimeMonitoring $false (a policy or tamper "
               "protection may turn it off again; check those first).",
    ),
    "av_signatures_old": _detection(
        "security", "Antivirus definitions are out of date", "warning",
        "Microsoft Defender's definitions are over 7 days old and no other antivirus is registered.",
        monitors=["defender_update_failed"],
        remedy="Update the definitions with Update-MpSignature.",
    ),
    "firewall_profile_off": _detection(
        "security", "A firewall profile is off", "warning", "The Domain, Private or Public firewall profile is turned off.",
        monitors=["firewall_changed"],
        remedy="Turn the profiles back on with Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True.",
    ),
    "battery_condition": _detection(
        "battery", "The battery needs service", "warning", "The battery reports a condition other than good or normal.",
        platforms=_BOTH,
    ),
    "battery_worn": _detection(
        "battery", "The battery is worn", "warning",
        "The battery holds under 70% of its design capacity (critical under 50%).", platforms=_BOTH,
    ),
    "pawnio_not_removed": _detection(
        "hardware", "PawnIO was not removed after a health check", "warning",
        "The tray app installed PawnIO to read sensors but could not uninstall it afterwards.",
    ),
}


def detection(key: Any) -> dict[str, Any] | None:
    item = DETECTIONS.get(str(key or ""))
    return {"key": str(key), **item} if item else None


def monitor(key: Any) -> dict[str, Any] | None:
    item = MONITORS.get(str(key or ""))
    return {"key": str(key), "origin": "catalog", **item} if item else None


def _signature(conditions: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted(
        (str(c.get("field")), str(c.get("operator")), str(c.get("value") or "").replace(" ", "").casefold())
        for c in conditions
    ))


def monitors_for_signals(signals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The catalog monitors for the detected problems, in the signals' order (most severe first)."""

    found: dict[str, dict[str, Any]] = {}
    for signal in signals:
        item = DETECTIONS.get(str(signal.get("key") or ""))
        for key in (item or {}).get("monitors", []):
            entry = found.setdefault(key, {**monitor(key), "detections": []})  # type: ignore[dict-item]
            title = str(signal.get("title") or "")
            if title and title not in entry["detections"]:
                entry["detections"].append(title)
    return list(found.values())


def parse_model_monitors(
    entries: Any, platform: str, *, known: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Event log monitors the model suggested, as valid event log rule conditions.

    Windows only (other agents send no event logs). Each needs a log and an
    event ID or source; anything the rule form would refuse is dropped, as
    are monitors with the same conditions as one in ``known``.
    """

    if platform != "windows" or not isinstance(entries, list):
        return []
    seen = {_signature(item.get("conditions") or []) for item in known}
    monitors: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        log = ai_troubleshooter.plain_text(entry.get("log"), 255)
        if not log or not rmm_event_logs.valid_log_name(log):
            continue
        ids: list[str] = []
        for value in entry.get("event_ids") if isinstance(entry.get("event_ids"), list) else [entry.get("event_ids")]:
            try:
                number = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if 0 < number < 65536 and str(number) not in ids:
                ids.append(str(number))
        ids = ids[:MAX_EVENT_IDS]
        source = ai_troubleshooter.plain_text(entry.get("source"), 255)
        if not ids and not source:
            continue
        rows = [("log_name", "is", log)]
        if source:
            rows.append(("provider", "is", source))
        if ids:
            rows.append(("event_id", "is" if len(ids) == 1 else "one_of", ", ".join(ids)))
        level = str(entry.get("level") or "").strip().lower()
        if level in {"critical", "error", "warning"}:
            rows.append(("level", "at_least", level.title()))
        message = ai_troubleshooter.plain_text(entry.get("message_contains"), 200)
        if message:
            rows.append(("message", "contains", message))
        try:
            conditions = rmm_event_log_rules.parse_conditions(*zip(*rows))
        except rmm_event_log_rules.RuleError:
            continue
        signature = _signature(conditions)
        if signature in seen:
            continue
        seen.add(signature)
        monitors.append({
            "key": None,
            "origin": "model",
            "name": ai_troubleshooter.plain_text(entry.get("title"), 120) or f"{log} events",
            "log": log,
            "conditions": conditions,
            "why": ai_troubleshooter.plain_text(entry.get("why"), 400),
            "detections": [],
        })
        if len(monitors) >= MAX_MODEL_MONITORS:
            break
    return monitors


def condition_text(condition: Mapping[str, Any]) -> str:
    return rmm_event_log_rules.describe_condition(condition)


def present_monitor(item: Mapping[str, Any], collected: Sequence[str] | None = None) -> dict[str, Any]:
    """A monitor for a page: its conditions in words and whether its log is collected."""

    logs = {name.casefold() for name in (collected if collected is not None else rmm_event_logs.default_logs())}
    return {
        **item,
        "conditions_text": [condition_text(c) for c in item.get("conditions") or []],
        "log_collected": str(item.get("log") or "").casefold() in logs,
    }


def rule_prefill(item: Mapping[str, Any]) -> dict[str, Any]:
    """The event log rule form's values for a monitor: raise a ticket when it matches."""

    return {
        "name": str(item.get("name") or "Health check monitor")[:255],
        "action": "create_ticket",
        "conditions": [
            {"field": str(c.get("field")), "operator": str(c.get("operator")), "value": str(c.get("value") or "")}
            for c in (item.get("conditions") or [])[: rmm_event_log_rules.MAX_CONDITIONS]
            if isinstance(c, Mapping)
        ],
        "log": str(item.get("log") or ""),
    }


__all__ = [
    "DETECTIONS",
    "MONITORS",
    "condition_text",
    "detection",
    "monitor",
    "monitors_for_signals",
    "parse_model_monitors",
    "present_monitor",
    "rule_prefill",
]
