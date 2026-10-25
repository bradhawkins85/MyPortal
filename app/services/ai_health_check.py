"""AI health check for the computer behind an asset.

A technician starts a health check from the asset page's Actions menu. It is
an RMM option, offered only when the asset has the RMM agent deployed. It
uses the AI troubleshooter's settings - the same language model
(``TROUBLESHOOT_LLM_*``), public web search (``TROUBLESHOOT_WEB_SEARCH_*``),
internal knowledge base and approved log list - and works in stages:

1. **Snapshot** - a ``health_check`` command asks the asset's tray device for
   a snapshot of the computer's health from built-in Windows metrics: disks
   and their reliability counters and failure prediction, volume free space
   and dirty (chkdsk) bits, defrag/TRIM state and fragmentation of hard
   disks, thermal zones, CPU throttling, memory and paging, boot performance,
   crashes, unexpected shutdowns and hardware (WHEA) errors, problem devices,
   updates, security and battery wear (macOS sends a smaller snapshot). On
   physical Windows hardware, when allowed, the device installs the PawnIO
   driver for the length of the check to read hardware sensors (CPU, GPU and
   motherboard temperatures, fans, voltages) and removes it again before it
   reports back. The device also sends a fixed set of health-related logs.
2. **Triage** - fixed rules flag problems in the snapshot; the language model
   reviews the snapshot, the flags and the logs, picks search terms and may
   ask for more allowlisted logs, which the device then collects.
3. **Report** - the model, given any public web pages and knowledge base
   articles found for the issues, writes a report with findings and the
   ordered steps that bring the computer back to optimal performance.

Logs the model wanted but could not have (not on the approved list, or
unreadable on the endpoint) are recorded for admins on ``/admin/required-logs``
(:mod:`app.services.ai_required_logs`).

Tray apps older than :data:`MIN_TRAY_VERSION` ignore the ``health_check``
command, so on such a device the command stays queued and the tray app is
asked to update; it goes out once the updated tray app reports its version.

The model never decides what runs on the endpoint: log requests are checked
against the approved list here and again on the device, and the snapshot
script is fixed and read-only.
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.ai_identity import get_agent_name
from app.core.config import get_settings
from app.core.logging import log_error, log_info, log_warning
from app.repositories import ai_health_checks as repo
from app.repositories import rmm as rmm_repo
from app.repositories import tray as tray_repo
from app.services import ai_prompt_security
from app.services import ai_required_logs
from app.services import ai_troubleshooter
from app.services import rmm_agent_deploy
from app.services import tray as tray_service
from app.services import troubleshoot_web_search

LLM_FEATURE = "ai_health_check"
COMMAND = "health_check"
# The first tray release whose service handles the health_check command. An
# older tray app silently ignores it, so the command stays queued until the
# device reports this version or later.
MIN_TRAY_VERSION = (0, 9, 10)
PHASE_SNAPSHOT = "snapshot"
PHASE_LOGS = "logs"

STATUS_QUEUED = "queued"
STATUS_COLLECTING = "collecting"
STATUS_ANALYSING = "analysing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
ACTIVE_STATUSES = frozenset({STATUS_QUEUED, STATUS_COLLECTING, STATUS_ANALYSING})
STATUS_LABELS = {
    STATUS_QUEUED: "Waiting for the device",
    STATUS_COLLECTING: "Collecting",
    STATUS_ANALYSING: "Analysing",
    STATUS_COMPLETED: "Completed",
    STATUS_FAILED: "Failed",
}

OVERALL_LABELS = {"healthy": "Healthy", "attention": "Needs attention", "critical": "Critical", "unknown": "Unknown"}
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

AREAS: dict[str, str] = {
    "storage": "Drives and storage",
    "chkdsk": "File system (chkdsk)",
    "defrag": "Optimisation (defrag and TRIM)",
    "thermals": "Thermals and cooling",
    "performance": "Performance",
    "memory": "Memory",
    "stability": "Stability and crashes",
    "hardware": "Hardware errors",
    "drivers": "Devices and drivers",
    "updates": "Updates and restarts",
    "security": "Security",
    "battery": "Battery",
    "services": "Services",
    "network": "Network",
    "os": "Operating system",
}

# Tray commands that never reach the device, or never report back, expire.
QUEUED_EXPIRY_HOURS = 24
RUNNING_EXPIRY_HOURS = 2

DEFAULT_SNAPSHOT_EVENT_DAYS = 7
MAX_EXTRA_LOG_REQUESTS = 6
MAX_STEPS = 15
MAX_FINDINGS = 15

_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
_MAX_SNAPSHOT_STRING = 2000
_MAX_SNAPSHOT_LIST = 300
_MAX_SNAPSHOT_DEPTH = 10
_SNAPSHOT_PROMPT_CHARS = 36 * 1024
_LOG_PROMPT_CHARS = 40 * 1024
_STORED_LOG_CHARS = 96 * 1024

# Health-related logs read with every snapshot. Keep within the tray agent's
# eight-source limit; the model can ask for more after triage.
BASELINE_LOG_REQUESTS: dict[str, list[dict[str, Any]]] = {
    "windows": [
        {"source": "System", "hours": 72,
         "reason": "Disk, NTFS, driver, hardware (WHEA) and unexpected shutdown errors"},
        {"source": "Application", "hours": 72,
         "reason": "Application crashes and hangs, chkdsk (Wininit) and defrag results"},
        {"source": "Microsoft-Windows-Diagnostics-Performance/Operational", "hours": 72,
         "reason": "Slow boot, shutdown and standby, and the apps, drivers and services that caused them"},
        {"source": "Microsoft-Windows-Kernel-Power/Thermal-Operational", "hours": 72,
         "reason": "Overheating and thermal throttling"},
        {"source": "Microsoft-Windows-Ntfs/Operational", "hours": 72,
         "reason": "File system corruption and self-healing"},
        {"source": "Microsoft-Windows-Storage-Storport/Operational", "hours": 72,
         "reason": "Disk timeouts, resets and slow I/O that come before drive failure"},
        {"source": "Microsoft-Windows-Resource-Exhaustion-Detector/Operational", "hours": 72,
         "reason": "Low memory and the processes using it"},
        {"source": "Microsoft-Windows-Kernel-WHEA/Errors", "hours": 72,
         "reason": "Hardware errors from the CPU, memory and PCIe devices"},
    ],
    "macos": [
        {"source": "macos:kernel", "hours": 24, "reason": "Kernel panics, driver and storage errors"},
        {"source": "macos:power", "hours": 24, "reason": "Sleep, wake, thermal pressure and battery events"},
        {"source": "macos:install", "hours": 72, "reason": "Software update installs and failures"},
        {"source": "file:/Library/Logs/DiagnosticReports/*.panic", "hours": 72,
         "reason": "Kernel panic reports"},
    ],
}

PAWNIO_LABELS = {
    "installed_and_removed": "PawnIO was installed for the check and removed afterwards.",
    "already_installed": "PawnIO was already installed and was left as it was.",
    "skipped_virtual": "Not installed: the computer is a virtual machine.",
    "skipped_disabled": "Not installed: PawnIO is turned off for this check.",
    "skipped_unsupported": "Not installed: PawnIO is only used on Windows.",
    "bundle_missing": "Not installed: this tray build does not include the sensor bundle.",
    "install_failed": "PawnIO could not be installed; built-in sensors were used.",
    "removal_failed": "PawnIO was installed but could not be removed. Remove it manually.",
    "identify_failed": "Not installed: the hardware could not be identified as physical.",
}

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class HealthCheckError(Exception):
    """A health check cannot be started; ``status_code`` suits an HTTP reply."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any, limit: int) -> str:
    return ai_troubleshooter.plain_text(value, limit)


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _schedule(coro: Awaitable[Any], *, description: str, health_check_id: int) -> asyncio.Task[Any]:
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)

    def _done(completed: asyncio.Task[Any]) -> None:
        _BACKGROUND_TASKS.discard(completed)
        if completed.cancelled():
            return
        exc = completed.exception()
        if exc is not None:
            log_error(f"AI health check {description} failed", health_check_id=health_check_id, error=str(exc))

    task.add_done_callback(_done)
    return task


async def _chat(llm: Mapping[str, str], messages: list[dict[str, str]]) -> str:
    return await ai_troubleshooter._chat(llm, messages, json_mode=True, feature=LLM_FEATURE)


def report_url(health_check_id: int) -> str:
    return f"/health-checks/{int(health_check_id)}"


def baseline_log_requests(platform: str) -> list[dict[str, Any]]:
    if platform == "unknown":
        return []
    return [dict(request) for request in BASELINE_LOG_REQUESTS.get(platform, [])]


def pawnio_allowed(requested: bool | None) -> bool:
    """PawnIO needs the owner's setting and, when given, the technician's choice."""

    enabled = bool(getattr(get_settings(), "health_check_pawnio_enabled", True))
    return enabled if requested is None else enabled and bool(requested)


def pawnio_label(snapshot: Mapping[str, Any] | None) -> str:
    info = _dict((snapshot or {}).get("pawnio"))
    status = str(info.get("status") or "")
    label = PAWNIO_LABELS.get(status, "")
    detail = _clean(info.get("detail"), 300)
    if status in {"install_failed", "removal_failed", "identify_failed"} and detail:
        label = f"{label} ({detail})"
    return label


# ---------------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------------


def _sanitise(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_SNAPSHOT_DEPTH:
        return None
    if isinstance(value, dict):
        return {
            str(key)[:80]: _sanitise(item, depth + 1)
            for key, item in list(value.items())[:_MAX_SNAPSHOT_LIST]
        }
    if isinstance(value, list):
        return [_sanitise(item, depth + 1) for item in value[:_MAX_SNAPSHOT_LIST]]
    if isinstance(value, str):
        return ai_troubleshooter.scrub_log_text(value[:_MAX_SNAPSHOT_STRING])
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_SNAPSHOT_STRING]


def parse_snapshot(raw: str | bytes | None) -> dict[str, Any]:
    """Parse and redact the device's snapshot JSON (bounded; ``{}`` if unusable)."""

    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw[:_MAX_SNAPSHOT_BYTES].decode("utf-8", errors="replace")
    elif len(raw) > _MAX_SNAPSHOT_BYTES:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _sanitise(data)


def collect_log_samples(
    log_text: str, requests: Sequence[Mapping[str, Any]], *, phase: str
) -> list[dict[str, Any]]:
    """Split a log bundle into per-source samples kept for the model and the report."""

    sections = ai_troubleshooter.split_log_sections(log_text)
    if not sections:
        return []
    reasons = {str(r.get("source") or ""): str(r.get("reason") or "") for r in requests}
    budget = max(2048, _STORED_LOG_CHARS // max(1, len(sections)))
    samples: list[dict[str, Any]] = []
    for source, body in sections.items():
        reason = reasons.get(source) or next(
            (text for request, text in reasons.items() if ai_troubleshooter.file_source_matches(request, source)),
            "Collected by the endpoint agent.",
        )
        unavailable = ai_required_logs.unavailable_detail(body)
        text, truncated = ai_troubleshooter._sample_log(ai_troubleshooter.scrub_log_text(body), budget)
        samples.append({
            "source": source[:400],
            "reason": reason[:400],
            "phase": phase,
            "text": text,
            "truncated": truncated,
            "unavailable": unavailable,
        })
    return samples


def _sections(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return _dict(snapshot.get("sections"))


# ---------------------------------------------------------------------------
# Fixed health rules
# ---------------------------------------------------------------------------


def _signal(area: str, severity: str, title: str, detail: str = "") -> dict[str, str]:
    return {"area": area, "severity": severity, "title": title[:300], "detail": detail[:600]}


def _drive(volume: Mapping[str, Any]) -> str:
    return str(volume.get("drive") or volume.get("mount") or volume.get("name") or "a volume")


def _disk_rules(sections: Mapping[str, Any], signals: list[dict[str, str]]) -> None:
    for volume in _list(sections.get("volumes")):
        if not isinstance(volume, dict):
            continue
        free = _num(volume.get("free_percent"))
        free_gb = _num(volume.get("free_gb"))
        name = _drive(volume)
        if free is not None and free < 5:
            signals.append(_signal("storage", "critical", f"{name} is almost full ({free:g}% free)",
                                   "Windows and apps slow down or fail when a drive has under 5% free."))
        elif free is not None and free < 10:
            signals.append(_signal("storage", "warning", f"{name} is low on space ({free:g}% free)"))
        elif free_gb is not None and free_gb < 5 and volume.get("boot_volume"):
            signals.append(_signal("storage", "warning", f"{name} has only {free_gb:g} GB free"))
        if volume.get("dirty") is True:
            signals.append(_signal("chkdsk", "warning", f"{name} is marked dirty",
                                   "The file system needs checking; chkdsk runs at the next restart."))
        health = str(volume.get("health") or "")
        if health in {"Warning", "Unhealthy"}:
            signals.append(_signal("chkdsk", "critical" if health == "Unhealthy" else "warning",
                                   f"Windows reports {name} as {health.lower()}"))
    for disk in _list(sections.get("disks")):
        if not isinstance(disk, dict):
            continue
        name = str(disk.get("name") or f"Disk {disk.get('device_id', '')}").strip()
        health = str(disk.get("health") or "")
        statuses = [str(item) for item in _list(disk.get("operational_status"))]
        if health == "Unhealthy" or any(s in {"Predictive Failure", "Error", "Lost Communication"} for s in statuses):
            signals.append(_signal("storage", "critical", f"{name} is failing or about to fail",
                                   f"Health: {health or 'unknown'}; status: {', '.join(statuses) or 'unknown'}."))
        elif health == "Warning" or "Degraded" in statuses:
            signals.append(_signal("storage", "warning", f"Windows reports a problem with {name}",
                                   f"Health: {health or 'unknown'}; status: {', '.join(statuses) or 'unknown'}."))
        if disk.get("smart_status") == "Failing":
            signals.append(_signal("storage", "critical", f"SMART reports {name} is failing"))
        uncorrected = sum(
            _num(disk.get(key)) or 0 for key in ("read_errors_uncorrected", "write_errors_uncorrected")
        )
        if uncorrected > 0:
            signals.append(_signal("storage", "critical", f"{name} has {int(uncorrected)} uncorrected read/write errors",
                                   "Uncorrected errors mean data could not be read or written; back up now."))
        wear = _num(disk.get("wear_percent"))
        if wear is not None and wear >= 90:
            signals.append(_signal("storage", "critical", f"{name} is {wear:g}% worn out"))
        elif wear is not None and wear >= 70:
            signals.append(_signal("storage", "warning", f"{name} is {wear:g}% worn out"))
        temp = _num(disk.get("temperature_c"))
        hot = 55 if str(disk.get("media_type") or "") == "HDD" else 70
        if temp is not None and temp >= hot:
            signals.append(_signal("thermals", "warning", f"{name} is running hot ({temp:g} °C)"))
    for prediction in _list(sections.get("smart_predictions")):
        if isinstance(prediction, dict) and prediction.get("predict_failure") is True:
            signals.append(_signal("storage", "critical", "A drive's SMART data predicts it will fail",
                                   f"{prediction.get('instance') or ''} reason {prediction.get('reason') or ''}".strip()))
    chkdsk = _dict(sections.get("chkdsk"))
    ntfs = _num(chkdsk.get("ntfs_errors"))
    if ntfs:
        signals.append(_signal("chkdsk", "warning", f"{int(ntfs)} NTFS file system warnings or errors recently"))
    disk_errors = _num(chkdsk.get("disk_errors"))
    if disk_errors:
        signals.append(_signal("storage", "critical" if disk_errors >= 10 else "warning",
                               f"{int(disk_errors)} disk I/O errors recently",
                               "Bad blocks, retried I/O or controller resets often come before a drive fails."))
    if chkdsk.get("scheduled") is True:
        signals.append(_signal("chkdsk", "info", "A disk check is scheduled for the next restart"))


def _defrag_rules(sections: Mapping[str, Any], signals: list[dict[str, str]]) -> None:
    defrag = _dict(sections.get("defrag"))
    if not defrag:
        return
    task = _dict(defrag.get("scheduled_task"))
    if task.get("enabled") is False:
        signals.append(_signal("defrag", "warning", "Scheduled drive optimisation is turned off"))
    if defrag.get("trim_enabled") is False:
        signals.append(_signal("defrag", "warning", "TRIM is turned off for SSDs"))
    for analysis in _list(defrag.get("analysis")):
        if not isinstance(analysis, dict):
            continue
        fragmentation = _num(analysis.get("total_fragmentation_percent"))
        if analysis.get("defrag_recommended") is True or (fragmentation is not None and fragmentation >= 10):
            signals.append(_signal("defrag", "warning", f"{_drive(analysis)} is fragmented"
                                   + (f" ({fragmentation:g}%)" if fragmentation is not None else "")))


def _thermal_rules(snapshot: Mapping[str, Any], sections: Mapping[str, Any], signals: list[dict[str, str]]) -> None:
    thermal = _dict(sections.get("thermal"))
    for zone in _list(thermal.get("zones")):
        if not isinstance(zone, dict):
            continue
        celsius = _num(zone.get("celsius"))
        name = str(zone.get("zone") or "A thermal zone")
        if celsius is not None and celsius >= 90:
            signals.append(_signal("thermals", "critical", f"{name} is at {celsius:g} °C"))
        elif celsius is not None and celsius >= 80:
            signals.append(_signal("thermals", "warning", f"{name} is at {celsius:g} °C"))
        limit = _num(zone.get("passive_limit_percent"))
        if limit is not None and 0 < limit < 100:
            signals.append(_signal("thermals", "warning", f"{name} is throttling the CPU to {limit:g}%"))
    events = _num(thermal.get("thermal_events"))
    if events:
        signals.append(_signal("thermals", "warning", f"{int(events)} thermal events recently"))
    speed_limit = _num(thermal.get("cpu_speed_limit"))
    if speed_limit is not None and 0 < speed_limit < 100:
        signals.append(_signal("thermals", "warning", f"macOS is limiting the CPU to {speed_limit:g}% of its speed",
                               "The Mac is too hot or its power supply is limited."))
    sensors = _dict(snapshot.get("sensors"))
    hottest: dict[str, float] = {}
    for reading in _list(sensors.get("readings")):
        if not isinstance(reading, dict) or str(reading.get("type") or "") != "Temperature":
            continue
        value = _num(reading.get("value"))
        hardware_type = str(reading.get("hardware_type") or "")
        if value is None or hardware_type not in {"Cpu", "GpuNvidia", "GpuAmd", "GpuIntel"}:
            continue
        group = "CPU" if hardware_type == "Cpu" else "GPU"
        hottest[group] = max(hottest.get(group, value), value)
    for group, value in hottest.items():
        if value >= 95:
            signals.append(_signal("thermals", "critical", f"{group} temperature is {value:g} °C",
                                   "At this temperature the processor throttles hard and may shut down."))
        elif value >= 85:
            signals.append(_signal("thermals", "warning", f"{group} temperature is {value:g} °C"))
    for reading in _list(sensors.get("readings")):
        if (isinstance(reading, dict) and str(reading.get("type") or "") == "Fan"
                and _num(reading.get("value")) == 0 and hottest.get("CPU", 0) >= 70):
            signals.append(_signal("thermals", "warning", f"{reading.get('name') or 'A fan'} reads 0 RPM while the CPU is hot"))
            break


def _performance_rules(sections: Mapping[str, Any], signals: list[dict[str, str]]) -> None:
    cpu = _dict(sections.get("cpu"))
    load = _num(cpu.get("load_percent"))
    if load is not None and load >= 90:
        signals.append(_signal("performance", "warning", f"CPU is busy ({load:g}% used)"))
    limit = _num(cpu.get("percent_performance_limit"))
    if limit is not None and 0 < limit < 90:
        signals.append(_signal("thermals", "warning", f"CPU performance is limited to {limit:g}%",
                               "Thermal or power limits are holding the processor back."))
    dpc = (_num(cpu.get("dpc_percent")) or 0) + (_num(cpu.get("interrupt_percent")) or 0)
    if dpc >= 10:
        signals.append(_signal("drivers", "warning", f"Drivers use {dpc:g}% of CPU time in interrupts/DPCs",
                               "A faulty driver often causes stutter and high DPC time."))
    logical = _num(cpu.get("logical_processors")) or 0
    queue = _num(cpu.get("processor_queue_length"))
    if queue is not None and logical and queue > 2 * logical:
        signals.append(_signal("performance", "warning", f"{int(queue)} threads are waiting for the CPU"))
    memory = _dict(sections.get("memory"))
    used = _num(memory.get("used_percent"))
    commit = _num(memory.get("commit_percent"))
    if commit is not None and commit >= 97:
        signals.append(_signal("memory", "critical", f"Committed memory is at {commit:g}% of the limit"))
    elif commit is not None and commit >= 90:
        signals.append(_signal("memory", "warning", f"Committed memory is at {commit:g}% of the limit"))
    if used is not None and used >= 90:
        signals.append(_signal("memory", "warning", f"Memory is {used:g}% used"))
    pages = _num(memory.get("pages_per_sec"))
    if pages is not None and pages >= 1000:
        signals.append(_signal("memory", "warning", f"Heavy paging ({int(pages)} pages/s)"))
    total = _num(memory.get("total_mb"))
    if total is not None and 0 < total < 7800:
        signals.append(_signal("memory", "info", f"Only {total / 1024:.1f} GB of memory installed"))
    performance = _dict(sections.get("performance"))
    boots = [_num(b.get("boot_ms")) for b in _list(performance.get("boots")) if isinstance(b, dict)]
    boots = [value for value in boots if value]
    if boots and boots[0] >= 120000:
        signals.append(_signal("performance", "warning", f"Last boot took {boots[0] / 1000:.0f} seconds"))
    stability_index = _num(performance.get("stability_index"))
    if stability_index is not None and stability_index < 5:
        signals.append(_signal("stability", "warning", f"Reliability Monitor stability index is {stability_index:g} of 10"))
    component = _dict(sections.get("component_store"))
    state = str(component.get("state") or "")
    if state == "repairable":
        signals.append(_signal("os", "warning", "The Windows component store is damaged but repairable"))
    elif state == "not_repairable":
        signals.append(_signal("os", "critical", "The Windows component store is damaged and cannot be repaired"))


def _stability_rules(sections: Mapping[str, Any], signals: list[dict[str, str]]) -> None:
    stability = _dict(sections.get("stability"))
    days = int(_num(stability.get("window_days")) or 30)
    bugchecks = _num(stability.get("bugcheck_count"))
    if bugchecks:
        signals.append(_signal("stability", "critical", f"{int(bugchecks)} blue screen crashes in {days} days"))
    panics = _num(stability.get("panic_count"))
    if panics:
        signals.append(_signal("stability", "critical", f"{int(panics)} kernel panics in {days} days"))
    shutdowns = _num(stability.get("unexpected_shutdowns"))
    if shutdowns:
        signals.append(_signal("stability", "warning", f"{int(shutdowns)} unexpected shutdowns or power losses in {days} days"))
    whea = _num(stability.get("whea_errors"))
    if whea:
        signals.append(_signal("hardware", "critical", f"{int(whea)} hardware (WHEA) errors in {days} days",
                               "WHEA errors come from the CPU, memory or PCIe devices and often precede hardware failure."))
    crashes = _num(stability.get("app_crashes"))
    if crashes and crashes >= 10:
        signals.append(_signal("stability", "warning", f"{int(crashes)} application crashes in {days} days"))
    services = _num(stability.get("service_crashes"))
    if services and services >= 5:
        signals.append(_signal("services", "warning", f"Services stopped unexpectedly {int(services)} times in {days} days"))
    drivers = _dict(sections.get("drivers"))
    problems = _num(drivers.get("problem_count"))
    if problems:
        signals.append(_signal("drivers", "warning", f"{int(problems)} devices have a driver or hardware problem"))
    system = _dict(sections.get("system"))
    uptime = _num(system.get("uptime_hours"))
    if uptime is not None and uptime >= 720:
        signals.append(_signal("updates", "warning", f"Not restarted for {uptime / 24:.0f} days"))
    updates = _dict(sections.get("updates"))
    if any(value is True for value in _dict(updates.get("reboot_pending")).values()):
        signals.append(_signal("updates", "warning", "A restart is waiting to finish installing updates"))
    last_hotfix = _dict(updates.get("last_hotfix"))
    installed = ai_troubleshooter._as_naive_utc(last_hotfix.get("installed_on"))
    if installed and _utcnow() - installed > timedelta(days=60):
        signals.append(_signal("updates", "warning", f"No updates installed for {(_utcnow() - installed).days} days"))
    failed = _num(updates.get("failed_updates"))
    if failed:
        signals.append(_signal("updates", "warning", f"{int(failed)} update installs failed recently"))


def _security_rules(sections: Mapping[str, Any], signals: list[dict[str, str]]) -> None:
    security = _dict(sections.get("security"))
    defender = _dict(security.get("defender"))
    other_av = [p for p in _list(security.get("antivirus_products"))
                if isinstance(p, dict) and "defender" not in str(p.get("name") or "").lower()]
    if defender and defender.get("realtime") is False and not other_av:
        signals.append(_signal("security", "critical", "Real-time antivirus protection is off"))
    age = _num(defender.get("signature_age_days"))
    if defender and age is not None and age > 7 and not other_av:
        signals.append(_signal("security", "warning", f"Antivirus definitions are {int(age)} days old"))
    for profile in _list(security.get("firewall")):
        if isinstance(profile, dict) and profile.get("enabled") is False:
            signals.append(_signal("security", "warning", f"The {profile.get('profile') or ''} firewall profile is off".replace("  ", " ")))
    battery = _dict(sections.get("battery"))
    condition = str(battery.get("condition") or "")
    if battery.get("present") and condition and condition.lower() not in {"good", "normal"}:
        signals.append(_signal("battery", "warning", f"Battery condition: {condition}"))
    health = _num(battery.get("health_percent"))
    if battery.get("present") and health is not None:
        if health < 50:
            signals.append(_signal("battery", "critical", f"Battery holds {health:g}% of its design capacity"))
        elif health < 70:
            signals.append(_signal("battery", "warning", f"Battery holds {health:g}% of its design capacity"))


def evaluate_snapshot(snapshot: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Fixed rules over the snapshot, most severe first."""

    snapshot = snapshot or {}
    sections = _sections(snapshot)
    signals: list[dict[str, str]] = []
    if not sections:
        return signals
    for rule in (
        lambda: _disk_rules(sections, signals),
        lambda: _defrag_rules(sections, signals),
        lambda: _thermal_rules(snapshot, sections, signals),
        lambda: _performance_rules(sections, signals),
        lambda: _stability_rules(sections, signals),
        lambda: _security_rules(sections, signals),
    ):
        try:
            rule()
        except Exception as exc:  # noqa: BLE001 - one bad value never hides the rest
            log_warning("AI health check rule failed", error=str(exc)[:300])
    pawnio = _dict(snapshot.get("pawnio"))
    if pawnio.get("status") == "removal_failed":
        signals.append(_signal("hardware", "warning", "PawnIO could not be removed after the check",
                               "Uninstall PawnIO from Apps & features."))
    signals.sort(key=lambda s: SEVERITY_RANK.get(s["severity"], 9))
    return signals


def score(signals: Sequence[Mapping[str, Any]]) -> tuple[int, str]:
    """Health score (0-100) and overall status from the rule flags."""

    penalty = {"critical": 20, "warning": 7, "info": 1}
    value = 100 - sum(penalty.get(str(s.get("severity")), 0) for s in signals)
    severities = {str(s.get("severity")) for s in signals}
    overall = "critical" if "critical" in severities else "attention" if "warning" in severities else "healthy"
    return max(0, min(100, value)), overall


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TRIAGE_INSTRUCTIONS = """You are a senior IT support engineer at a managed-service provider
reviewing a health check of one computer. You have a snapshot of the computer's built-in
health metrics (and hardware sensor readings when available), problems already flagged
by fixed rules, and recent logs.

Respond with a single JSON object, no other text:
{"issues": [{"area": str, "title": str, "severity": "critical"|"warning"|"info", "evidence": str}],
 "search_terms": [str, ...],
 "log_requests": [{"source": str, "reason": str, "hours": int}]}

Rules:
- "issues": the problems affecting this computer's health or performance, most severe
  first (at most 12). "area" is one of AREAS. Quote the exact snapshot values or log lines
  (with timestamps) in "evidence". Never invent readings or log entries.
- Look especially for impending drive failure, file system errors, overheating and
  throttling, crashes and unexpected shutdowns, hardware (WHEA) errors, slow boot,
  memory pressure, problem drivers, missing updates and security gaps.
- "search_terms": 0-4 short, generic phrases to look up fixes for the most important
  issues (for example "Windows 11 Kernel-Power 41 unexpected restart" or "NVMe SSD
  overheating throttling"). Never include names, hostnames, serial numbers, IP or email
  addresses.
- "log_requests": only logs that are not in ALREADY_COLLECTED and would confirm or rule
  out a cause (at most 6). "source" is one of ALLOWED_LOG_SOURCES exactly, or a log file
  as "file:<absolute path>" inside ALLOWED_LOG_FILE_FOLDERS ending in one of
  ALLOWED_LOG_FILE_EXTENSIONS (a * wildcard may stand for a file or folder name). "hours"
  is how far back to read (1-72). If you need a log that is not allowed, still request it
  with its exact event log channel name or full file path: it is not collected but is
  listed for an administrator to approve. Return [] when no more logs are needed."""

_REPORT_INSTRUCTIONS = """You are a senior IT support engineer at a managed-service provider
writing a health report for one computer. You have its health snapshot, the problems
flagged by fixed rules, the issues found in triage, recent logs and, when available,
public web pages and internal knowledge base articles about those issues.

Respond with a single JSON object, no other text:
{"overall_status": "healthy"|"attention"|"critical",
 "summary": str,
 "areas": [{"area": str, "status": "good"|"warning"|"critical"|"unknown", "summary": str}],
 "findings": [{"title": str, "severity": "critical"|"high"|"medium"|"low", "area": str,
               "evidence": str, "source": str}],
 "steps": [{"action": str, "detail": str, "priority": "now"|"soon"|"routine", "area": str,
            "reference": str}],
 "required_logs": [{"source": str, "reason": str}]}

Rules:
- "summary": two to four sentences a technician can read first: overall condition, the
  most important problems and what to do first.
- "areas": one entry per AREAS key the data covers (thermals, drives, chkdsk, defrag,
  performance, stability, hardware errors, updates, security, battery...). Use "unknown"
  when the data is missing; never guess.
- "findings": at most 15, most severe first. Quote exact values or log lines in
  "evidence"; "source" names the snapshot section or log.
- "steps": the ordered troubleshooting steps that bring this computer back to optimal
  performance, most important first (at most 15). Be concrete: what to open, run or
  change. Put "back up the data" before any step on a failing drive, and say when a step
  needs a restart or downtime. "reference" is the title or URL of the web page or
  knowledge base article the step comes from, or "".
- "required_logs": logs or data you did not have that would confirm or rule out a cause
  (an event log channel name or a full log file path), with why. Return [] if none.
- Never invent readings, log entries or URLs."""


def _areas_text() -> str:
    return "\nAREAS: " + ", ".join(AREAS)


def _snapshot_for_prompt(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Bound the snapshot for the prompt, trimming the largest sections first."""

    compact = dict(snapshot)
    sections = dict(_sections(snapshot))
    compact["sections"] = sections
    encoded = json.dumps(compact, default=str, separators=(",", ":"))
    if len(encoded) <= _SNAPSHOT_PROMPT_CHARS:
        return compact
    sizes = sorted(
        ((len(json.dumps(value, default=str)), key) for key, value in sections.items()), reverse=True
    )
    for size, key in sizes:
        if len(json.dumps(compact, default=str, separators=(",", ":"))) <= _SNAPSHOT_PROMPT_CHARS:
            break
        text = json.dumps(sections[key], default=str, separators=(",", ":"))
        sections[key] = {"truncated": True, "json": text[: max(1500, _SNAPSHOT_PROMPT_CHARS // 12)]}
    readings = _list(_dict(compact.get("sensors")).get("readings"))
    if len(json.dumps(compact, default=str, separators=(",", ":"))) > _SNAPSHOT_PROMPT_CHARS and readings:
        sensors = dict(_dict(compact.get("sensors")))
        sensors["readings"] = [r for r in readings if isinstance(r, dict)
                               and r.get("type") in {"Temperature", "Fan", "Level"}][:120]
        compact["sensors"] = sensors
    return compact


def _log_records(samples: Sequence[Mapping[str, Any]]) -> list[ai_prompt_security.UntrustedRecord]:
    usable = [s for s in samples if isinstance(s, Mapping)]
    if not usable:
        return []
    budget = max(1500, _LOG_PROMPT_CHARS // len(usable))
    records = []
    for sample in usable:
        text, truncated = ai_troubleshooter._sample_log(str(sample.get("text") or ""), budget)
        records.append(ai_prompt_security.UntrustedRecord(
            record_id=f"log-{sample.get('source')}",
            provenance=f"Redacted endpoint log: {sample.get('source')}",
            content={
                "source": sample.get("source"),
                "reason_collected": sample.get("reason"),
                "truncated": bool(truncated or sample.get("truncated")),
                "entries": text or "[no entries in this window]",
            },
            allowed_use="Evidence for the computer's health",
        ))
    return records


def _base_records(check: Mapping[str, Any], signals: Sequence[Mapping[str, Any]]) -> list[Any]:
    snapshot = _dict(check.get("snapshot"))
    return [
        ai_prompt_security.UntrustedRecord(
            record_id="snapshot",
            provenance="Health snapshot reported by the computer's tray agent (redacted)",
            content=_snapshot_for_prompt(snapshot) if snapshot else {"note": "The device sent no snapshot."},
            allowed_use="Evidence for the computer's health",
        ),
        ai_prompt_security.UntrustedRecord(
            record_id="automatic-checks",
            provenance="Problems flagged by MyPortal's fixed health rules from the snapshot",
            content=list(signals),
            allowed_use="Evidence for the computer's health",
        ),
    ]


def _allowed_text(platform: str, collected: Sequence[str]) -> str:
    allowed = ai_troubleshooter.allowed_log_sources(platform)
    return (
        f"\n\nENDPOINT_PLATFORM: {platform}\nALLOWED_LOG_SOURCES:\n"
        + "\n".join(f"- {key}: {description}" for key, description in allowed.items())
        + "\nALLOWED_LOG_FILE_FOLDERS:\n"
        + "\n".join(
            f"- {key}: {description}"
            for key, description in ai_troubleshooter.allowed_log_file_roots(platform).items()
        )
        + f"\nALLOWED_LOG_FILE_EXTENSIONS: {ai_troubleshooter.LOG_FILE_EXTENSIONS}"
        + "\nALREADY_COLLECTED:\n"
        + "\n".join(f"- {source}" for source in collected)
    )


def _parse_issues(data: Mapping[str, Any] | None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for entry in _list((data or {}).get("issues")):
        if not isinstance(entry, Mapping):
            continue
        title = _clean(entry.get("title"), 300)
        if not title:
            continue
        severity = str(entry.get("severity") or "").lower()
        issues.append({
            "area": _area(entry.get("area")),
            "title": title,
            "severity": severity if severity in {"critical", "warning", "info"} else "warning",
            "evidence": str(entry.get("evidence") or "").strip()[:1200],
        })
        if len(issues) >= 12:
            break
    return issues


def _area(value: Any) -> str:
    key = re.sub(r"[^a-z]", "", str(value or "").lower())
    aliases = {
        "drives": "storage", "disk": "storage", "disks": "storage", "thermal": "thermals",
        "temperature": "thermals", "filesystem": "chkdsk", "optimisation": "defrag",
        "optimization": "defrag", "crashes": "stability", "whea": "hardware", "hardwareerrors": "hardware",
        "update": "updates", "driver": "drivers", "operatingsystem": "os",
    }
    key = aliases.get(key, key)
    return key if key in AREAS else "os"


def _parse_report(data: Mapping[str, Any] | None, raw_text: str) -> dict[str, Any]:
    if not data:
        text = _clean(raw_text, 4000)
        return {"summary": text, "areas": [], "findings": [], "steps": [], "required_logs": [],
                "overall_status": None}
    areas = []
    for entry in _list(data.get("areas")):
        if not isinstance(entry, Mapping):
            continue
        status = str(entry.get("status") or "").lower()
        areas.append({
            "area": _area(entry.get("area")),
            "status": status if status in {"good", "warning", "critical", "unknown"} else "unknown",
            "summary": _clean(entry.get("summary"), 600),
        })
        if len(areas) >= len(AREAS):
            break
    findings = []
    for entry in _list(data.get("findings")):
        if not isinstance(entry, Mapping):
            continue
        title = _clean(entry.get("title"), 300)
        if not title:
            continue
        severity = str(entry.get("severity") or "").lower()
        findings.append({
            "title": title,
            "severity": severity if severity in {"critical", "high", "medium", "low"} else "medium",
            "area": _area(entry.get("area")),
            "evidence": str(entry.get("evidence") or "").strip()[:1500],
            "source": _clean(entry.get("source"), 200),
        })
        if len(findings) >= MAX_FINDINGS:
            break
    steps = []
    for entry in _list(data.get("steps")):
        if isinstance(entry, Mapping):
            action = _clean(entry.get("action"), 400)
            detail = _clean(entry.get("detail"), 1200)
            priority = str(entry.get("priority") or "").lower()
            area = _area(entry.get("area"))
            reference = str(entry.get("reference") or "").strip()[:300]
        else:
            action, detail, priority, area, reference = _clean(entry, 400), "", "soon", "os", ""
        if not action:
            continue
        steps.append({
            "action": action,
            "detail": detail,
            "priority": priority if priority in {"now", "soon", "routine"} else "soon",
            "area": area,
            "reference": reference,
            "reference_url": ai_troubleshooter._safe_url(reference),
        })
        if len(steps) >= MAX_STEPS:
            break
    overall = str(data.get("overall_status") or "").lower()
    return {
        "summary": _clean(data.get("summary"), 2000),
        "overall_status": overall if overall in {"healthy", "attention", "critical"} else None,
        "areas": areas,
        "findings": findings,
        "steps": steps,
        "required_logs": _list(data.get("required_logs")),
    }


def _worst(*statuses: str | None) -> str:
    order = ["critical", "attention", "healthy"]
    present = [s for s in statuses if s in order]
    return min(present, key=order.index) if present else "unknown"


# ---------------------------------------------------------------------------
# Starting a health check
# ---------------------------------------------------------------------------


async def expire_stale() -> int:
    """Fail checks whose device never answered, so a new check can start."""

    try:
        stale = await repo.list_stale(queued_hours=QUEUED_EXPIRY_HOURS, running_hours=RUNNING_EXPIRY_HOURS)
    except Exception as exc:  # noqa: BLE001 - best-effort housekeeping
        log_warning("Could not look for stale health checks", error=str(exc)[:300])
        return 0
    for check in stale:
        check_id = int(check["id"])
        waiting_for_logs = bool(check.get("has_snapshot")) and check.get("status") != STATUS_ANALYSING
        if waiting_for_logs:
            message = "The device did not send the extra logs in time; the report uses what it already sent."
        elif check.get("status") == STATUS_QUEUED:
            message = f"The device did not pick up the health check within {QUEUED_EXPIRY_HOURS} hours."
        else:
            message = (
                f"The health check did not finish within {RUNNING_EXPIRY_HOURS} hours. The tray app may have "
                "gone offline, or be too old to run health checks (update it and try again)."
            )
        command_id = _int_or_none(check.get("command_id"))
        if command_id:
            command = await tray_repo.get_command(command_id)
            if command and str(command.get("status") or "") in {"queued", "delivered"}:
                await tray_repo.mark_command_completed(command_id, error=message)
        if waiting_for_logs:
            await repo.update(check_id, status=STATUS_ANALYSING, error=message, stage_message="Writing the report.")
            _schedule(run_report(check_id), description="report", health_check_id=check_id)
            continue
        await repo.update(check_id, status=STATUS_FAILED, error=message, completed=True)
    return len(stale)


def tray_can_run(device: Mapping[str, Any]) -> bool:
    """Whether the device's tray app handles the health_check command."""

    return rmm_agent_deploy.tray_version_at_least(device, MIN_TRAY_VERSION)


async def ready_for_device(device_id: int) -> bool:
    """Whether a queued health check may go to this device now (read fresh from the database,
    because the tray app reports its new version after it reconnects)."""

    device = await tray_repo.get_device_by_id(device_id)
    return bool(device) and tray_can_run(device)


async def dispatch_command(
    *, device: Mapping[str, Any], payload: dict[str, Any], initiated_by_user_id: int | None
) -> tuple[int, bool]:
    """Log a ``health_check`` tray command and deliver it now if the device is connected.

    An offline device receives it from the queue when it reconnects; a device
    whose tray app is too old receives it once the updated tray app reports
    its version.
    """

    command_id = await tray_repo.log_command(
        device_id=int(device["id"]),
        command=COMMAND,
        payload_json=json.dumps(payload),
        initiated_by_user_id=initiated_by_user_id,
        status="queued",
    )
    device_uid = str(device.get("device_uid") or "").strip()
    delivered = bool(device_uid) and tray_can_run(device) and await tray_service.send_to_device(
        device_uid, {"type": COMMAND, "command_id": command_id, **payload}
    )
    if delivered:
        await tray_repo.mark_command_delivered(command_id)
    return command_id, delivered


async def _ticket_note(ticket_id: Any, body_html: str) -> None:
    ticket = _int_or_none(ticket_id)
    if not ticket:
        return
    try:
        await tray_service.add_troubleshoot_note(ticket, body_html)
    except Exception as exc:  # noqa: BLE001 - notes are best-effort
        log_error("Failed to add AI health check note", ticket_id=ticket, error=str(exc))


def _note_header() -> str:
    return f"<p><strong>{html.escape(get_agent_name())}</strong> - "


async def rmm_agent_deployed(asset_id: int, company_id: int) -> bool:
    """True when the asset has an active RMM agent in its own company."""

    agent = await rmm_repo.get_asset_agent(int(asset_id))
    return bool(agent) and int(agent.get("company_id") or 0) == int(company_id)


async def start_health_check(
    *,
    asset: Mapping[str, Any],
    device: Mapping[str, Any],
    requested_by: Mapping[str, Any] | None,
    ticket: Mapping[str, Any] | None = None,
    allow_pawnio: bool | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Create a health check for ``asset`` and send the snapshot command to its device.

    ``asset`` needs ``id`` (or ``asset_id``), ``company_id`` and ``name``;
    ``device`` is the asset's active tray device in the same company. The
    health check is an RMM option: the asset must have the RMM agent deployed.
    """

    asset_id = _int_or_none(asset.get("asset_id") or asset.get("id"))
    company_id = _int_or_none(asset.get("company_id") or device.get("company_id"))
    if not asset_id or not company_id:
        raise HealthCheckError("The asset is missing its company.", 400)
    if not await rmm_agent_deployed(asset_id, company_id):
        raise HealthCheckError("Deploy the RMM agent to this computer first; the health check is an RMM option.")
    if device.get("status") != "active":
        raise HealthCheckError("The asset's tray device is not active.")
    if int(device.get("company_id") or 0) != company_id:
        raise HealthCheckError("The tray device belongs to a different company.", 403)
    await expire_stale()
    active = await repo.get_active_for_asset(asset_id)
    if active:
        raise HealthCheckError(f"A health check is already running for this asset (#{active['id']}).")
    llm = tray_service.build_troubleshoot_llm_config(str(model or "").strip() or None)
    if not llm["model"]:
        raise HealthCheckError("No LLM model is configured for the AI assistant.")
    platform = ai_troubleshooter.endpoint_platform(device, asset)
    pawnio = pawnio_allowed(allow_pawnio) and platform == "windows"
    user_id = _int_or_none((requested_by or {}).get("id"))
    ticket_id = _int_or_none((ticket or {}).get("id"))
    check_id = await repo.create(
        company_id=company_id, asset_id=asset_id, tray_device_id=_int_or_none(device.get("id")),
        ticket_id=ticket_id, platform=platform, model=llm["model"], allow_pawnio=pawnio,
        requested_by_user_id=user_id,
    )
    await ai_required_logs.refresh_approvals()
    requests = baseline_log_requests(platform)
    payload = {
        "health_check_id": check_id,
        "phase": PHASE_SNAPSHOT,
        "log_requests": requests,
        "extra_log_folders": ai_troubleshooter.extra_log_folders(platform),
        "extra_event_logs": ai_troubleshooter.extra_event_logs(platform),
        "allow_pawnio": pawnio,
        "event_days": DEFAULT_SNAPSHOT_EVENT_DAYS,
    }
    command_id, delivered = await dispatch_command(device=device, payload=payload, initiated_by_user_id=user_id)
    status = STATUS_COLLECTING if delivered else STATUS_QUEUED
    if delivered:
        message = "The device is collecting the health snapshot."
    elif not tray_can_run(device):
        message = (
            f"The tray app on this computer ({device.get('agent_version')}) is too old to run health checks "
            f"(version {rmm_agent_deploy.version_label(MIN_TRAY_VERSION)} or later is needed). It has been asked "
            "to update; the health check runs once the updated tray app reports its version."
        )
        try:
            await tray_service.dispatch_update_command(dict(device), initiated_by_user_id=user_id)
        except Exception as exc:  # noqa: BLE001 - the check still runs once the tray app updates
            log_warning("Could not ask the tray app to update for a health check",
                        health_check_id=check_id, error=str(exc)[:300])
    else:
        message = "The device is offline; it will run the health check when it reconnects."
    await repo.update(check_id, command_id=command_id, status=status, stage_message=message, log_requests=requests)
    asset_name = str(asset.get("name") or f"asset #{asset_id}")
    if ticket_id:
        await _ticket_note(
            ticket_id,
            _note_header()
            + "health check requested by "
            + html.escape(tray_service.troubleshoot_requester_label(dict(requested_by or {})))
            + f" for {html.escape(asset_name)}, model {html.escape(llm['model'])}. "
            + f'<a href="{report_url(check_id)}">Open the health check</a>.</p>'
            + f"<p>{html.escape(message)}</p>",
        )
    log_info(
        "AI health check started",
        health_check_id=check_id, asset_id=asset_id, command_id=command_id, delivered=delivered,
        pawnio=pawnio, ticket_id=ticket_id,
    )
    return {"id": check_id, "command_id": command_id, "delivered": delivered, "status": status,
            "model": llm["model"], "allow_pawnio": pawnio}


# ---------------------------------------------------------------------------
# Device callbacks
# ---------------------------------------------------------------------------

DEVICE_STAGES = {
    "received": "The device started the health check",
    "collecting_snapshot": "Reading built-in health metrics",
    "reading_sensors": "Reading hardware sensors",
    "collecting_logs": "Collecting logs",
    "uploading": "Uploading the results",
    "failed": "The health check failed on the device",
}


async def record_device_status(check: Mapping[str, Any], command: Mapping[str, Any], stage: str, message: str) -> None:
    heading = DEVICE_STAGES[stage]
    text = f"{heading}: {message}" if message else heading
    if stage == "failed":
        await tray_repo.mark_command_completed(int(command["id"]), error=(message or heading)[:1000])
        if check.get("snapshot"):
            # Only the extra logs failed: report on what the device already sent.
            await repo.update(int(check["id"]), status=STATUS_ANALYSING, stage_message="Writing the report.",
                              error=f"The extra logs could not be collected: {message or heading}")
            _schedule(run_report(int(check["id"])), description="report", health_check_id=int(check["id"]))
            return
        await repo.update(int(check["id"]), status=STATUS_FAILED, stage_message=text, error=message or heading,
                          completed=True)
        await _ticket_note(check.get("ticket_id"), _note_header() + "health check failed on the device: "
                           + html.escape(message or heading) + "</p>")
        return
    fields: dict[str, Any] = {"stage_message": text}
    if check.get("status") == STATUS_QUEUED:
        fields["status"] = STATUS_COLLECTING
    await repo.update(int(check["id"]), **fields)
    if command.get("status") == "queued":
        await tray_repo.mark_command_delivered(int(command["id"]))


def _record_unavailable_entries(samples: Sequence[Mapping[str, Any]], requests: Sequence[Mapping[str, Any]],
                                sections_present: bool) -> list[dict[str, str]]:
    entries = [
        {"source": str(s["source"]), "reason_code": ai_required_logs.REASON_UNAVAILABLE,
         "reason": str(s.get("reason") or ""), "detail": str(s["unavailable"])}
        for s in samples if s.get("unavailable")
    ]
    if sections_present:
        present = [str(s["source"]) for s in samples]
        for request in requests:
            source = str(request.get("source") or "")
            if source and source not in present and not any(
                ai_troubleshooter.file_source_matches(source, item) for item in present
            ):
                entries.append({
                    "source": source, "reason_code": ai_required_logs.REASON_UNAVAILABLE,
                    "reason": str(request.get("reason") or ""),
                    "detail": "The endpoint agent did not collect this source (it is not on the agent's own "
                    "allowlist, or the agent is out of date).",
                })
    return entries


async def receive_device_result(
    check: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    phase: str,
    snapshot_text: str | bytes | None,
    log_bytes: bytes,
    error: str,
) -> bool:
    """Store what the device sent and queue the next stage. Returns True when queued."""

    check_id = int(check["id"])
    payload = ai_troubleshooter.command_payload(command)
    requests = [r for r in payload.get("log_requests") or [] if isinstance(r, Mapping)]
    log_text = ai_troubleshooter.decompress_log_bundle(log_bytes)
    samples = collect_log_samples(log_text, requests, phase=phase)
    unavailable = _record_unavailable_entries(samples, requests, bool(log_text.strip()))
    if unavailable:
        await ai_required_logs.record(
            unavailable, feature=ai_required_logs.FEATURE_HEALTH_CHECK, platform=str(check.get("platform") or ""),
            company_id=check.get("company_id"), asset_id=check.get("asset_id"), ticket_id=check.get("ticket_id"),
            health_check_id=check_id,
        )
    logs = _dict(check.get("logs"))
    stored = [s for s in _list(logs.get("sources")) if isinstance(s, dict)]
    stored += samples
    fields: dict[str, Any] = {"logs": {"sources": stored}, "status": STATUS_ANALYSING}
    error_text = error.strip()[:1000]
    if phase == PHASE_SNAPSHOT:
        snapshot = parse_snapshot(snapshot_text)
        fields["snapshot"] = snapshot
        fields["pawnio_status"] = pawnio_label(snapshot) or None
        fields["stage_message"] = "Snapshot received; reviewing the computer's health."
        if not snapshot and not samples:
            fields.update(status=STATUS_FAILED, completed=True,
                          error=error_text or "The device sent no snapshot and no logs.")
            await repo.update(check_id, **fields)
            await tray_repo.mark_command_completed(int(command["id"]), error=fields["error"])
            await _ticket_note(check.get("ticket_id"), _note_header() + "health check failed: "
                               + html.escape(fields["error"]) + "</p>")
            return False
    else:
        fields["stage_message"] = f"{len(samples)} more log(s) received; writing the report."
    if error_text:
        fields["error"] = error_text
    await repo.update(check_id, **fields)
    await tray_repo.mark_command_completed(int(command["id"]))
    if phase == PHASE_SNAPSHOT:
        _schedule(run_triage(check_id), description="triage", health_check_id=check_id)
    else:
        _schedule(run_report(check_id), description="report", health_check_id=check_id)
    return True


# ---------------------------------------------------------------------------
# Triage and report
# ---------------------------------------------------------------------------


async def _fail(check: Mapping[str, Any], message: str) -> None:
    await repo.update(int(check["id"]), status=STATUS_FAILED, error=message, completed=True)
    await _ticket_note(check.get("ticket_id"), _note_header() + "health check failed: " + html.escape(message) + "</p>")


async def _device_for(check: Mapping[str, Any]) -> dict[str, Any] | None:
    device_id = _int_or_none(check.get("tray_device_id"))
    if not device_id:
        return None
    device = await tray_repo.get_device_by_id(device_id)
    if not device or device.get("status") != "active":
        return None
    if int(device.get("company_id") or 0) != int(check.get("company_id") or 0):
        return None
    return dict(device)


async def run_triage(health_check_id: int) -> None:
    """Review the snapshot, then ask for more logs or go straight to the report."""

    check = await repo.get(health_check_id)
    if not check or check.get("status") != STATUS_ANALYSING:
        return
    platform = str(check.get("platform") or "unknown")
    signals = evaluate_snapshot(check.get("snapshot"))
    samples = [s for s in _list(_dict(check.get("logs")).get("sources")) if isinstance(s, dict)]
    collected = [str(s.get("source")) for s in samples]
    llm = tray_service.build_troubleshoot_llm_config(str(check.get("model") or "") or None)
    await ai_required_logs.refresh_approvals()
    allowed = ai_troubleshooter.allowed_log_sources(platform)
    triage: dict[str, Any] = {"issues": [], "search_terms": [], "refused_logs": [], "requested_logs": []}
    try:
        text = await _chat(llm, ai_prompt_security.build_messages(
            _TRIAGE_INSTRUCTIONS + _areas_text() + _allowed_text(platform, collected),
            [*_base_records(check, signals), *_log_records(samples)],
            task="Review this computer's health and decide whether more logs are needed.",
        ))
    except Exception as exc:  # noqa: BLE001 - continue to the report without triage
        triage["error"] = str(exc)[:300] or exc.__class__.__name__
        log_warning("AI health check triage failed", health_check_id=health_check_id, error=triage["error"])
        text = ""
    data = ai_troubleshooter._extract_json_object(text) if text else None
    triage["issues"] = _parse_issues(data)
    triage["search_terms"] = ai_troubleshooter._string_list((data or {}).get("search_terms"), limit=4, item_chars=80)
    requests, refused = ai_troubleshooter.parse_log_requests(
        (data or {}).get("log_requests"), allowed, platform, default_reason="Requested by the health check."
    )
    requests = [r for r in requests if r["source"] not in collected][:MAX_EXTRA_LOG_REQUESTS]
    triage["refused_logs"] = refused
    if refused:
        await ai_required_logs.record(
            [{**entry, "reason_code": ai_required_logs.REASON_NOT_ALLOWED} for entry in refused],
            feature=ai_required_logs.FEATURE_HEALTH_CHECK, platform=platform, company_id=check.get("company_id"),
            asset_id=check.get("asset_id"), ticket_id=check.get("ticket_id"), health_check_id=health_check_id,
        )
    device = await _device_for(check) if requests else None
    if requests and device:
        triage["requested_logs"] = requests
        payload = {
            "health_check_id": health_check_id,
            "phase": PHASE_LOGS,
            "log_requests": requests,
            "extra_log_folders": ai_troubleshooter.extra_log_folders(platform),
            "extra_event_logs": ai_troubleshooter.extra_event_logs(platform),
            "allow_pawnio": False,
        }
        command_id, delivered = await dispatch_command(
            device=device, payload=payload, initiated_by_user_id=_int_or_none(check.get("requested_by_user_id"))
        )
        await repo.update(
            health_check_id, report={"triage": triage}, command_id=command_id,
            status=STATUS_COLLECTING if delivered else STATUS_QUEUED,
            log_requests=[*_list(check.get("log_requests")), *requests],
            stage_message=f"Collecting {len(requests)} more log(s): " + ", ".join(r["source"] for r in requests),
        )
        return
    await repo.update(health_check_id, report={"triage": triage}, stage_message="Writing the report.")
    await run_report(health_check_id)


async def _research(check: Mapping[str, Any], terms: Sequence[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    kb_articles: list[dict[str, Any]] = []
    web_pages: list[dict[str, Any]] = []
    web_error: str | None = None
    if not terms:
        return kb_articles, web_pages, web_error
    kb_articles = await ai_troubleshooter._search_kb({"company_id": check.get("company_id"), "subject": ""}, terms)
    if troubleshoot_web_search.is_enabled():
        try:
            web_pages = await troubleshoot_web_search.research(list(terms))
        except Exception as exc:  # noqa: BLE001 - reported in the report
            web_error = str(exc)[:300] or exc.__class__.__name__
    return kb_articles, web_pages, web_error


async def run_report(health_check_id: int) -> None:
    """Write the final report: findings and the steps back to optimal performance."""

    check = await repo.get(health_check_id)
    if not check or check.get("status") not in {STATUS_ANALYSING, STATUS_COLLECTING}:
        return
    await repo.update(health_check_id, status=STATUS_ANALYSING, stage_message="Writing the report.")
    platform = str(check.get("platform") or "unknown")
    snapshot = _dict(check.get("snapshot"))
    signals = evaluate_snapshot(snapshot)
    rule_score, rule_overall = score(signals)
    report = dict(_dict(check.get("report")))
    triage = _dict(report.get("triage"))
    samples = [s for s in _list(_dict(check.get("logs")).get("sources")) if isinstance(s, dict)]
    collected = [str(s.get("source")) for s in samples]
    terms = [str(t) for t in _list(triage.get("search_terms"))]
    kb_articles, web_pages, web_error = await _research(check, terms)
    records = [*_base_records(check, signals)]
    if triage.get("issues"):
        records.append(ai_prompt_security.UntrustedRecord(
            record_id="triage", provenance="Issues found when the snapshot was first reviewed",
            content=triage["issues"], allowed_use="Evidence for the computer's health",
        ))
    records += _log_records(samples)
    records += [
        ai_prompt_security.UntrustedRecord(
            record_id=f"kb-{article['id']}", provenance="Internal knowledge base article",
            content={"title": article["title"], "summary": article["summary"], "content": article["content"]},
            allowed_use="Troubleshooting reference",
        )
        for article in kb_articles
    ] + [
        ai_prompt_security.UntrustedRecord(
            record_id=f"web-{index}", provenance=f"Public web page: {page['url']}",
            content={"title": page["title"], "url": page["url"], "text": page["content"]},
            allowed_use="Troubleshooting reference",
        )
        for index, page in enumerate(web_pages, start=1)
    ]
    llm = tray_service.build_troubleshoot_llm_config(str(check.get("model") or "") or None)
    ai_error: str | None = None
    try:
        text = await _chat(llm, ai_prompt_security.build_messages(
            _REPORT_INSTRUCTIONS + _areas_text() + f"\n\nENDPOINT_PLATFORM: {platform}",
            records,
            task="Write the health report and the steps that bring this computer back to optimal performance.",
        ))
        parsed = _parse_report(ai_troubleshooter._extract_json_object(text), text)
    except Exception as exc:  # noqa: BLE001 - the rule flags still make a report
        ai_error = str(exc)[:300] or exc.__class__.__name__
        log_warning("AI health check report failed", health_check_id=health_check_id, error=ai_error)
        parsed = {"summary": "", "overall_status": None, "areas": [], "findings": [], "steps": [], "required_logs": []}
    await ai_required_logs.refresh_approvals()
    allowed = ai_troubleshooter.allowed_log_sources(platform)
    wanted, refused = ai_troubleshooter.parse_log_requests(
        parsed.pop("required_logs"), allowed, platform, default_reason="Would help confirm a cause."
    )
    if refused:
        await ai_required_logs.record(
            [{**entry, "reason_code": ai_required_logs.REASON_NOT_ALLOWED} for entry in refused],
            feature=ai_required_logs.FEATURE_HEALTH_CHECK, platform=platform, company_id=check.get("company_id"),
            asset_id=check.get("asset_id"), ticket_id=check.get("ticket_id"), health_check_id=health_check_id,
        )
    overall = _worst(rule_overall, parsed.get("overall_status")) if snapshot or samples else "unknown"
    if not snapshot:
        overall = parsed.get("overall_status") or "unknown"
    summary = parsed.get("summary") or (
        f"{len(signals)} problem(s) flagged by the automatic checks." if signals
        else "The automatic checks found no problems."
    )
    report.update({
        "signals": signals,
        "summary": summary,
        "areas": parsed["areas"],
        "findings": parsed["findings"],
        "steps": parsed["steps"],
        "required_logs": {
            "not_allowed": [*_list(triage.get("refused_logs")), *refused],
            "not_collected": [w for w in wanted if w["source"] not in collected],
        },
        "kb_articles": [{"title": a["title"], "slug": a["slug"]} for a in kb_articles if a.get("slug")],
        "web_pages": [
            {"title": p["title"], "url": ai_troubleshooter._safe_url(p["url"])}
            for p in web_pages if ai_troubleshooter._safe_url(p["url"])
        ],
        "web_error": web_error,
        "ai_error": ai_error,
    })
    await repo.update(
        health_check_id, status=STATUS_COMPLETED, overall_status=overall,
        health_score=rule_score if snapshot else None, summary=summary, report=report, completed=True,
        stage_message="Report ready." if not ai_error else "Report ready (the AI model could not be reached).",
    )
    log_info("AI health check finished", health_check_id=health_check_id, overall=overall, score=rule_score)
    if check.get("ticket_id"):
        await _ticket_note(check.get("ticket_id"), report_note(check, overall, rule_score if snapshot else None, report))


def report_note(check: Mapping[str, Any], overall: str, health_score: int | None, report: Mapping[str, Any]) -> str:
    """The ticket note posted when a health check started from a ticket finishes."""

    parts = [
        _note_header() + "health check of " + html.escape(str(check.get("asset_name") or "the asset"))
        + f": <strong>{html.escape(OVERALL_LABELS.get(overall, overall))}</strong>"
        + (f" (score {int(health_score)}/100)" if health_score is not None else "")
        + f'. <a href="{report_url(int(check["id"]))}">Open the full report</a>.</p>'
    ]
    if report.get("summary"):
        parts.append(f"<p>{html.escape(str(report['summary']))}</p>")
    steps = [s for s in _list(report.get("steps")) if isinstance(s, Mapping)][:8]
    if steps:
        items = "".join(
            f"<li><strong>{html.escape(str(s.get('action') or ''))}</strong>"
            + (f"<br>{html.escape(str(s['detail']))}" if s.get("detail") else "")
            + "</li>"
            for s in steps
        )
        parts.append(f"<p><strong>Recommended steps</strong></p><ol>{items}</ol>")
    if report.get("ai_error"):
        parts.append("<p><em>The AI model could not be reached; the report shows the automatic checks only.</em></p>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def present(check: Mapping[str, Any]) -> dict[str, Any]:
    """Template-friendly view of a health check row."""

    item = dict(check)
    item["status_label"] = STATUS_LABELS.get(str(check.get("status") or ""), str(check.get("status") or ""))
    item["overall_label"] = OVERALL_LABELS.get(str(check.get("overall_status") or ""), "")
    item["is_active"] = check.get("status") in ACTIVE_STATUSES
    item["url"] = report_url(int(check["id"]))
    return item


__all__ = [
    "ACTIVE_STATUSES",
    "AREAS",
    "COMMAND",
    "HealthCheckError",
    "MIN_TRAY_VERSION",
    "PHASE_LOGS",
    "PHASE_SNAPSHOT",
    "baseline_log_requests",
    "collect_log_samples",
    "evaluate_snapshot",
    "expire_stale",
    "parse_snapshot",
    "present",
    "ready_for_device",
    "receive_device_result",
    "record_device_status",
    "report_note",
    "rmm_agent_deployed",
    "run_report",
    "run_triage",
    "score",
    "start_health_check",
    "tray_can_run",
]
