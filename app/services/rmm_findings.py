"""RMM findings: a platform-wide, best-guess look at RMM data for things
technicians should know about.

An analysis (every ``FINDINGS_INTERVAL_HOURS``, or when a super admin asks)
gathers signals from every company's RMM data:

* event logs: event types far more frequent in the last 24 hours than over the
  ``FINDINGS_BASELINE_DAYS`` before, account lockouts with the computers
  causing them, new user accounts, new services, members added to admin
  groups and cleared logs (read from the messages of the matching events)
* software: installs, removals and version changes, found by comparing each
  device's software with what it had at the previous analysis, and event
  spikes that started on the devices where a program changed
* stats and hardware: devices whose CPU or memory runs well above devices of
  the same spec, nearly full system drives, long uptimes, agents that stopped
  checking in and checks failing across many devices

Fixed rules turn the clearest signals into findings, so the page is useful
without a language model. The signals then go to the configured model
(``FINDINGS_LLM_*``, else the troubleshooter's LLM) for findings the rules
cannot see, such as correlations between signals. Model output is checked
before it is stored: only devices and companies that were in the signals are
kept.

The same finding found again updates its row (``rmm_findings`` keyed by a
fingerprint), so a technician's acknowledgement or dismissal sticks, and one
no longer found stops being active.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import httpx

from app.core.config import get_settings
from app.core.event_log_database import event_log_db
from app.core.logging import log_info, log_warning
from app.core.stats_database import stats_db
from app.repositories import rmm_findings as repo
from app.services import ai_prompt_security
from app.services import llm_usage
from app.services import singleton_jobs
from app.services import tray as tray_service
from app.services.monitored_http import monitored_client

LLM_FEATURE = "rmm_findings"
LEASE_NAME = "rmm_findings"
LEASE_SECONDS = 1800
STALE_RUN_MINUTES = 45

CURRENT_WINDOW = timedelta(hours=24)

CATEGORIES: dict[str, str] = {
    "security": "Security",
    "events": "Event logs",
    "software": "Software changes",
    "performance": "Performance",
    "health": "Device health",
    "other": "Other",
}
SEVERITIES: dict[str, str] = {
    "critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info",
}
SEVERITY_RANK = {key: rank for rank, key in enumerate(SEVERITIES)}
STATUSES: dict[str, str] = {"open": "Open", "acknowledged": "Acknowledged", "dismissed": "Dismissed"}

# Event spikes: at least this many entries in the current window and this
# many times the baseline daily rate.
SPIKE_MIN_COUNT = 20
SPIKE_MIN_RATIO = 3.0
NEW_EVENT_MIN_COUNT = 10
NEW_EVENT_MIN_DEVICES = 3
MAX_SPIKES = 12

LOCKOUT_MIN = 3
SECURITY_EVENT_LIMIT = 5000
ACCOUNT_SIMILARITY = 0.85

LOW_DISK_BYTES = 5 * 1024 ** 3
CRITICAL_DISK_BYTES = 2 * 1024 ** 3
LONG_UPTIME_DAYS = 30
STALE_AGENT_DAYS = 14
PEER_MIN_DEVICES = 3
PEER_MIN_SAMPLES = 30

_LLM_TIMEOUT_SECONDS = 300
_LLM_RESPONSE_LIMIT_CHARS = 64 * 1024
MAX_AI_FINDINGS = 10

# (provider fragment, kind) for the event IDs read from their messages.
SECURITY_EVENTS: dict[int, tuple[str, str]] = {
    4740: ("security-auditing", "lockout"),
    4720: ("security-auditing", "account_created"),
    4728: ("security-auditing", "group_add"),
    4732: ("security-auditing", "group_add"),
    4756: ("security-auditing", "group_add"),
    1102: ("eventlog", "log_cleared"),
    104: ("eventlog", "log_cleared"),
    7045: ("service control manager", "service_installed"),
}

_SUSPICIOUS_SERVICE_PATH = re.compile(
    r"\\temp\\|\\appdata\\|\\users\\public\\|\\perflogs\\|\\downloads\\|powershell|cmd(\.exe)?\s+/c|%comspec%|"
    r"mshta|rundll32|regsvr32|\\\\[^\\]+\\admin\$",
    re.IGNORECASE,
)
_SECURITY_SOFTWARE = re.compile(
    r"defender|sentinel ?one|crowdstrike|falcon|huntress|sophos|eset|bitdefender|malwarebytes|webroot|"
    r"trend ?micro|kaspersky|symantec|norton|mcafee|carbon ?black|cylance|threatlocker|blackpoint|"
    r"cisco secure endpoint|datto (edr|av)|vipre|avast|avg ",
    re.IGNORECASE,
)
_REMOTE_ACCESS_SOFTWARE = re.compile(
    r"anydesk|teamviewer|screenconnect|connectwise control|splashtop|atera|ngrok|logmein|supremo|ultraviewer|"
    r"radmin|realvnc|tightvnc|ultravnc|tigervnc|remote utilities|dameware|zoho assist|getscreen|action1",
    re.IGNORECASE,
)

_background_tasks: set[asyncio.Task[Any]] = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


def _iso(value: Any) -> str | None:
    """UTC as ISO 8601 with a ``Z`` so browsers never read it as local time."""

    parsed = _as_datetime(value)
    return parsed.replace(microsecond=0).isoformat() + "Z" if parsed else None


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _clip(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _fingerprint(source: str, key: str) -> str:
    return hashlib.sha256(f"{source}:{key}".encode("utf-8")).hexdigest()


def _slug(value: str, limit: int = 100) -> str:
    return re.sub(r"[^a-z0-9:._-]+", "-", value.lower()).strip("-")[:limit]


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #


class Devices:
    """The active RMM agents, looked up by agent, asset or hostname."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        self.by_agent: dict[int, dict[str, Any]] = {}
        self._by_name: dict[tuple[int | None, str], int] = {}
        for row in rows:
            device = dict(row)
            agent_id = int(device["agent_id"])
            device["name"] = str(device.get("asset_name") or device.get("hostname") or f"Agent {agent_id}")
            self.by_agent[agent_id] = device
            for name in (device.get("hostname"), device.get("asset_name")):
                if name:
                    self._by_name[(device.get("company_id"), str(name).strip().lower())] = agent_id

    def __len__(self) -> int:
        return len(self.by_agent)

    def get(self, agent_id: Any) -> dict[str, Any] | None:
        try:
            return self.by_agent.get(int(agent_id))
        except (TypeError, ValueError):
            return None

    def find_by_name(self, name: str, company_id: int | None) -> dict[str, Any] | None:
        key = str(name or "").strip().lower().split(".")[0]
        if not key:
            return None
        agent_id = self._by_name.get((company_id, key))
        return self.by_agent.get(agent_id) if agent_id is not None else None

    def label(self, agent_id: Any) -> str:
        device = self.get(agent_id)
        if not device:
            return f"Agent {agent_id}"
        company = device.get("company_name")
        return f"{device['name']} ({company})" if company else device["name"]

    def asset_ids(self, agent_ids: Iterable[Any]) -> list[int]:
        return sorted({int(d["asset_id"]) for d in (self.get(a) for a in agent_ids) if d and d.get("asset_id")})

    def company_ids(self, agent_ids: Iterable[Any]) -> list[int]:
        return sorted({int(d["company_id"]) for d in (self.get(a) for a in agent_ids) if d and d.get("company_id")})

    def companies(self) -> set[int]:
        return {int(d["company_id"]) for d in self.by_agent.values() if d.get("company_id")}


def _finding(
    key: str,
    *,
    category: str,
    severity: str,
    title: str,
    summary: str,
    recommendation: str,
    evidence: Sequence[str],
    agent_ids: Iterable[Any] = (),
    devices: Devices,
    company_ids: Iterable[int] = (),
    source: str = "rule",
    confidence: int | None = None,
) -> dict[str, Any]:
    agents = list(agent_ids)
    return {
        "key": key,
        "fingerprint": _fingerprint(source, key),
        "source": source,
        "category": category,
        "severity": severity,
        "title": _clip(title, 255),
        "summary": summary.strip(),
        "recommendation": recommendation.strip(),
        "evidence": [_clip(line, 300) for line in evidence if line][:12],
        "confidence": confidence,
        "asset_ids": devices.asset_ids(agents),
        "company_ids": sorted(set(devices.company_ids(agents)) | {int(c) for c in company_ids if c}),
    }


# --------------------------------------------------------------------------- #
# Event log signals
# --------------------------------------------------------------------------- #


def _event_key(row: Mapping[str, Any]) -> tuple[str, str, int]:
    return str(row.get("log_name") or ""), str(row.get("provider") or ""), int(row.get("event_id") or 0)


def _level_weight(level: Any) -> float:
    return {1: 3.0, 2: 2.0, 3: 1.5}.get(int(level or 4), 1.0)


async def collect_event_spikes(devices: Devices, *, now: datetime, baseline_days: int) -> list[dict[str, Any]]:
    """Event types far more frequent in the last 24 hours than in the baseline."""

    current_start = now - CURRENT_WINDOW
    baseline_start = current_start - timedelta(days=baseline_days)
    current = await repo.event_counts(since=current_start, until=now)
    baseline = {_event_key(row): row for row in await repo.event_counts(since=baseline_start, until=current_start)}

    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in current:
        key = _event_key(row)
        total = int(row.get("total") or 0)
        base_total = int((baseline.get(key) or {}).get("total") or 0)
        daily = base_total / baseline_days
        ratio = (total + 1) / (daily + 1)
        devices_now = int(row.get("devices") or 0)
        is_new = base_total == 0 and total >= NEW_EVENT_MIN_COUNT and devices_now >= NEW_EVENT_MIN_DEVICES
        if not is_new and (total < SPIKE_MIN_COUNT or ratio < SPIKE_MIN_RATIO):
            continue
        score = math.log2(ratio + 1) * _level_weight(row.get("level")) * (1 + math.log2(devices_now + 1))
        candidates.append((score, {
            "log_name": key[0], "provider": key[1], "event_id": key[2], "level": int(row.get("level") or 4),
            "total": total, "devices": devices_now, "companies": int(row.get("companies") or 0),
            "baseline_daily": round(daily, 1), "ratio": round(ratio, 1), "is_new": is_new,
        }))
    candidates.sort(key=lambda item: item[0], reverse=True)

    spikes = []
    for _score, spike in candidates[:MAX_SPIKES]:
        rows = await repo.event_agent_counts(
            log_name=spike["log_name"], provider=spike["provider"], event_id=spike["event_id"],
            baseline_start=baseline_start, current_start=current_start, until=now,
        )
        per_device = []
        for row in rows:
            current_count = int(row.get("current_count") or 0)
            device_daily = int(row.get("baseline_count") or 0) / baseline_days
            if current_count and current_count >= max(3, 3 * device_daily):
                per_device.append({
                    "agent_id": int(row["agent_id"]), "current": current_count, "baseline_daily": round(device_daily, 1),
                    "first_seen": _as_datetime(row.get("first_current")),
                })
        per_device.sort(key=lambda item: item["current"], reverse=True)
        spike["spiking_devices"] = per_device
        spike["sample"] = _clip(await repo.event_sample(
            log_name=spike["log_name"], provider=spike["provider"], event_id=spike["event_id"], since=current_start,
        ), 240)
        spikes.append(spike)
    return spikes


def _section(message: str, marker: str) -> str:
    """The text after ``marker`` (a section heading such as ``New Account:``)."""

    index = message.lower().find(marker.lower())
    return message[index + len(marker):] if index >= 0 else ""


def _field(message: str, label: str, *, last: bool = False) -> str:
    """A ``Label:  value`` line's value from a Windows event message; '-' is empty."""

    pattern = re.compile(r"^[ \t]*" + re.escape(label) + r":[ \t]*(\S[^\r\n]*?)[ \t]*\r?$", re.MULTILINE | re.IGNORECASE)
    values = [match.group(1).strip() for match in pattern.finditer(message or "")]
    values = [value for value in values if value and value != "-"]
    if not values:
        return ""
    return values[-1] if last else values[0]


def _account(value: str) -> str:
    return value.split("\\")[-1].split("@")[0].strip()


def account_base(name: str) -> str:
    """A name reduced for comparison: lower case, letters only (``J.Smith2`` is ``jsmith``)."""

    return re.sub(r"[^a-z]", "", _account(name).lower().rstrip("$"))


def parse_security_event(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """What a security-relevant event says, or None for other sources."""

    definition = SECURITY_EVENTS.get(int(row.get("event_id") or 0))
    if not definition or definition[0] not in str(row.get("provider") or "").lower():
        return None
    message = str(row.get("message") or "")
    kind = definition[1]
    event: dict[str, Any] = {
        "kind": kind, "agent_id": int(row["agent_id"]), "company_id": row.get("company_id"),
        "time": _as_datetime(row.get("time_created")), "log_name": row.get("log_name"),
    }
    if kind == "lockout":
        event["account"] = _account(_field(_section(message, "Locked Out:") or message, "Account Name"))
        event["caller"] = _field(message, "Caller Computer Name")
    elif kind == "account_created":
        section = _section(message, "New Account:") or message
        event["account"] = _account(_field(section, "SAM Account Name") or _field(section, "Account Name"))
        event["creator"] = _account(_field(_section(message, "Subject:") or message, "Account Name"))
    elif kind == "group_add":
        member = _section(message, "Member:")
        event["member"] = _account(_field(member, "Account Name") or _field(member, "Security ID"))
        event["group"] = _field(_section(message, "Group:") or message, "Group Name")
    elif kind == "service_installed":
        event["service"] = _field(message, "Service Name")
        event["path"] = _field(message, "Service File Name")
        event["account"] = _field(message, "Service Account")
    return event


async def collect_security_events(*, now: datetime, baseline_days: int) -> list[dict[str, Any]]:
    since = now - CURRENT_WINDOW - timedelta(days=baseline_days)
    rows = await repo.security_events(event_ids=sorted(SECURITY_EVENTS), since=since, limit=SECURITY_EVENT_LIMIT)
    return [event for event in (parse_security_event(row) for row in rows) if event]


def group_lockouts(events: Sequence[Mapping[str, Any]], *, since: datetime) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, str], dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "lockout" or not event.get("account") or (event.get("time") and event["time"] < since):
            continue
        key = (event.get("company_id"), event["account"].lower())
        group = groups.setdefault(key, {"account": event["account"], "company_id": event.get("company_id"),
                                        "count": 0, "callers": Counter(), "agents": set()})
        group["count"] += 1
        group["callers"][event.get("caller") or "unknown"] += 1
        group["agents"].add(event["agent_id"])
    return sorted(groups.values(), key=lambda group: group["count"], reverse=True)


def cluster_accounts(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """New accounts grouped by similar names (``jsmith``, ``j.smith``, ``jsmith2``)."""

    by_base: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "account_created" or not event.get("account"):
            continue
        base = account_base(event["account"])
        if len(base) < 3:
            continue
        group = by_base.setdefault(base, {"names": set(), "agents": set(), "companies": set(), "creators": set()})
        group["names"].add(event["account"])
        group["agents"].add(event["agent_id"])
        if event.get("company_id"):
            group["companies"].add(int(event["company_id"]))
        if event.get("creator"):
            group["creators"].add(event["creator"])

    clusters: list[dict[str, Any]] = []
    for base in sorted(by_base, key=len):
        group = by_base[base]
        for cluster in clusters:
            if len(base) >= 4 and difflib.SequenceMatcher(None, cluster["base"], base).ratio() >= ACCOUNT_SIMILARITY:
                for field in ("names", "agents", "companies", "creators"):
                    cluster[field] |= group[field]
                break
        else:
            clusters.append({"base": base, **{field: set(group[field]) for field in group}})
    return sorted(clusters, key=lambda c: (len(c["companies"]), len(c["agents"])), reverse=True)


def group_services(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "service_installed" or not event.get("service"):
            continue
        group = groups.setdefault(event["service"].lower(), {
            "service": event["service"], "paths": set(), "agents": set(), "companies": set(), "first": event.get("time"),
        })
        if event.get("path"):
            group["paths"].add(event["path"])
        group["agents"].add(event["agent_id"])
        if event.get("company_id"):
            group["companies"].add(int(event["company_id"]))
        if event.get("time") and (group["first"] is None or event["time"] < group["first"]):
            group["first"] = event["time"]
    for group in groups.values():
        group["suspicious"] = any(_SUSPICIOUS_SERVICE_PATH.search(path) for path in group["paths"])
    return sorted(groups.values(), key=lambda g: (g["suspicious"], len(g["agents"])), reverse=True)


def group_admin_adds(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "group_add" or "admin" not in str(event.get("group") or "").lower():
            continue
        member = event.get("member") or "unknown"
        group = groups.setdefault(member.lower(), {"member": member, "groups": set(), "agents": set(), "companies": set()})
        group["groups"].add(event.get("group") or "")
        group["agents"].add(event["agent_id"])
        if event.get("company_id"):
            group["companies"].add(int(event["company_id"]))
    return sorted(groups.values(), key=lambda g: (len(g["companies"]), len(g["agents"])), reverse=True)


# --------------------------------------------------------------------------- #
# Software signals
# --------------------------------------------------------------------------- #


def _software_items(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    items: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        names.setdefault(name.lower(), name)
        items[name.lower()].add(str(row.get("version") or "").strip())
    return {names[key]: "|".join(sorted(versions)) for key, versions in items.items()}


def diff_software(
    previous: Mapping[str, str] | None,
    rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    since: datetime,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """The device's software now and what changed since ``previous``.

    Without a previous snapshot, programs whose install date falls after
    ``since`` count as installed on that date."""

    current = _software_items(rows)
    changes: list[dict[str, Any]] = []
    if previous is None:
        for row in rows:
            installed = _as_datetime(row.get("install_date"))
            if installed and installed >= since and row.get("name"):
                changes.append({"name": str(row["name"]), "change_type": "installed", "old_version": None,
                                "new_version": str(row.get("version") or "") or None, "detected_at": installed})
        return current, changes
    before = {name.lower(): (name, version) for name, version in previous.items()}
    after = {name.lower(): (name, version) for name, version in current.items()}
    for key, (name, version) in after.items():
        if key not in before:
            changes.append({"name": name, "change_type": "installed", "old_version": None,
                            "new_version": version or None, "detected_at": now})
        elif before[key][1] != version:
            changes.append({"name": name, "change_type": "updated", "old_version": before[key][1] or None,
                            "new_version": version or None, "detected_at": now})
    for key, (name, version) in before.items():
        if key not in after:
            changes.append({"name": name, "change_type": "removed", "old_version": version or None,
                            "new_version": None, "detected_at": now})
    return current, changes


async def record_software_changes(devices: Devices, *, now: datetime, since: datetime) -> int:
    """Compare each device's software with the previous analysis and store the changes."""

    agent_ids = sorted(devices.by_agent)
    stored = 0
    for start in range(0, len(agent_ids), 200):
        batch = agent_ids[start:start + 200]
        software = await repo.list_software(batch)
        snapshots = await repo.list_snapshots(batch)
        for agent_id in batch:
            rows = software.get(agent_id) or []
            if not rows:
                # Nothing collected (yet); never read an empty list as everything removed.
                continue
            current, changes = diff_software(snapshots.get(agent_id), rows, now=now, since=since)
            device = devices.get(agent_id) or {}
            changes = changes[:300]
            await repo.insert_software_changes({
                **change, "agent_id": agent_id, "company_id": device.get("company_id"), "asset_id": device.get("asset_id"),
            } for change in changes)
            await repo.save_snapshot(agent_id, current)
            stored += len(changes)
    return stored


def group_software_changes(rows: Iterable[Mapping[str, Any]], devices: Devices) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if devices.get(row.get("agent_id")) is None:
            continue
        key = (str(row["name"]).lower(), str(row["change_type"]), str(row.get("new_version") or ""))
        group = groups.setdefault(key, {
            "name": row["name"], "change_type": row["change_type"], "new_version": row.get("new_version"),
            "old_versions": set(), "agents": {}, "companies": set(),
        })
        if row.get("old_version"):
            group["old_versions"].add(str(row["old_version"]))
        detected = _as_datetime(row.get("detected_at"))
        agent_id = int(row["agent_id"])
        if agent_id not in group["agents"] or (detected and detected < group["agents"][agent_id]):
            group["agents"][agent_id] = detected
        if row.get("company_id"):
            group["companies"].add(int(row["company_id"]))
    return sorted(groups.values(), key=lambda g: len(g["agents"]), reverse=True)


def correlate(spikes: Sequence[Mapping[str, Any]], changes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Event spikes that started on the devices where a program was installed or updated.

    Inventory arrives about daily, so a change may be noticed up to 36 hours
    after the spike it caused began."""

    found = []
    for spike in spikes:
        spiking = {device["agent_id"]: device for device in spike.get("spiking_devices") or []}
        if len(spiking) < 2:
            continue
        best = None
        for change in changes:
            if change["change_type"] not in ("installed", "updated") or len(change["agents"]) < 2:
                continue
            overlap = []
            for agent_id, detected in change["agents"].items():
                device = spiking.get(agent_id)
                if not device:
                    continue
                first_seen = device.get("first_seen")
                if detected and first_seen and detected > first_seen + timedelta(hours=36):
                    continue
                overlap.append(agent_id)
            share = len(overlap) / len(spiking)
            if len(overlap) >= 2 and share >= 0.5 and (best is None or share > best["share"]):
                best = {"spike": spike, "change": change, "agents": overlap, "share": share}
        if best:
            found.append(best)
    return found


# --------------------------------------------------------------------------- #
# Performance and health signals
# --------------------------------------------------------------------------- #


def _cpu_family(model: Any) -> str:
    text = re.sub(r"\((r|tm)\)|cpu|processor|@.*$|\d+-core", " ", str(model or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _ram_bucket(ram_bytes: Any) -> int:
    try:
        gigabytes = int(ram_bytes or 0) / 1024 ** 3
    except (TypeError, ValueError):
        return 0
    if gigabytes <= 0:
        return 0
    return min((4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 256), key=lambda bucket: abs(bucket - gigabytes))


def peer_group(device: Mapping[str, Any]) -> tuple[str, int, str] | None:
    cpu, ram = _cpu_family(device.get("cpu_model")), _ram_bucket(device.get("ram_bytes"))
    if not cpu or not ram:
        return None
    return cpu, ram, str(device.get("os") or "").lower()


def find_performance_outliers(
    averages: Iterable[Mapping[str, Any]], devices: Devices
) -> list[dict[str, Any]]:
    """Devices whose average CPU or memory use is well above devices of the same spec."""

    values: dict[tuple[str, tuple[str, int, str]], dict[int, float]] = defaultdict(dict)
    for row in averages:
        device = devices.get(row.get("agent_id"))
        group = peer_group(device) if device else None
        if not group or int(row.get("samples") or 0) < PEER_MIN_SAMPLES:
            continue
        values[(str(row["metric"]), group)][int(row["agent_id"])] = float(row.get("average") or 0)

    outliers = []
    for (metric, group), by_agent in values.items():
        if len(by_agent) < PEER_MIN_DEVICES:
            continue
        median = statistics.median(by_agent.values())
        spread = statistics.median(abs(value - median) for value in by_agent.values()) * 1.4826
        floor = 50.0 if metric == "cpu_percent" else 80.0
        limit = median + max(20.0, 3 * spread)
        hot = {agent_id: value for agent_id, value in by_agent.items() if value >= limit and value >= floor}
        if hot:
            outliers.append({"metric": metric, "group": group, "median": median, "peers": len(by_agent), "devices": hot})
    return outliers


def health_signals(devices: Devices, *, now: datetime) -> dict[str, list[dict[str, Any]]]:
    low_disk, long_uptime, stale = [], [], []
    for agent_id, device in devices.by_agent.items():
        free = device.get("system_disk_free_bytes")
        if free is not None and int(free) < LOW_DISK_BYTES:
            low_disk.append({"agent_id": agent_id, "free_bytes": int(free)})
        booted = _as_datetime(device.get("last_boot_at"))
        seen = _as_datetime(device.get("last_seen_utc"))
        if booted and seen and seen >= now - timedelta(days=2) and booted < now - timedelta(days=LONG_UPTIME_DAYS):
            long_uptime.append({"agent_id": agent_id, "days": (now - booted).days})
        if seen and seen < now - timedelta(days=STALE_AGENT_DAYS):
            stale.append({"agent_id": agent_id, "days": (now - seen).days})
    low_disk.sort(key=lambda item: item["free_bytes"])
    long_uptime.sort(key=lambda item: item["days"], reverse=True)
    stale.sort(key=lambda item: item["days"], reverse=True)
    return {"low_disk": low_disk, "long_uptime": long_uptime, "stale_agents": stale}


def _gb(value: int) -> str:
    return f"{value / 1024 ** 3:.1f} GB"


# --------------------------------------------------------------------------- #
# Rule findings
# --------------------------------------------------------------------------- #


def _event_name(spike: Mapping[str, Any]) -> str:
    return f"{spike['provider'] or spike['log_name']} event {spike['event_id']}"


def spike_findings(spikes: Sequence[Mapping[str, Any]], devices: Devices, *, baseline_days: int) -> list[dict[str, Any]]:
    findings = []
    for spike in spikes:
        if spike["level"] > 3 and spike["devices"] < NEW_EVENT_MIN_DEVICES:
            continue
        severity = "high" if spike["level"] <= 2 and spike["devices"] >= 5 else (
            "medium" if spike["level"] <= 2 else "low")
        if spike["is_new"]:
            title = f"New {_event_name(spike)} on {_plural(spike['devices'], 'device')}"
            context = f"It was not seen in the previous {baseline_days} days."
        else:
            title = f"{_event_name(spike)} is {spike['ratio']:g}× more frequent than usual"
            context = f"Over the previous {baseline_days} days it averaged about {spike['baseline_daily']:g} a day."
        agents = [device["agent_id"] for device in spike["spiking_devices"]]
        findings.append(_finding(
            f"spike:{spike['log_name']}:{spike['provider']}:{spike['event_id']}".lower(),
            category="events", severity=severity, title=title, devices=devices, agent_ids=agents,
            summary=(f"{spike['total']} {spike['log_name']} log entries in the last 24 hours from "
                     f"{_plural(spike['devices'], 'device')} in {_plural(spike['companies'], 'company', 'companies')}. "
                     + context),
            recommendation=(f"Open Event logs for the busiest devices and filter on event ID {spike['event_id']}"
                            f"{' from ' + spike['provider'] if spike['provider'] else ''} to see what changed. "
                            "Look for a recent update, a new program or a failing service common to them."),
            evidence=[f"Example message: {spike['sample']}" if spike.get("sample") else ""] + [
                f"{devices.label(d['agent_id'])}: {d['current']} in 24 hours (about {d['baseline_daily']:g} a day before)"
                for d in spike["spiking_devices"][:8]
            ],
        ))
    return findings


def correlation_findings(correlations: Sequence[Mapping[str, Any]], devices: Devices) -> list[dict[str, Any]]:
    findings = []
    for item in correlations:
        spike, change = item["spike"], item["change"]
        version = f" {change['new_version']}" if change.get("new_version") else ""
        verb = "an update to" if change["change_type"] == "updated" else "installing"
        spiking = len(spike["spiking_devices"])
        findings.append(_finding(
            f"correlation:{spike['log_name']}:{spike['provider']}:{spike['event_id']}:{change['name']}:{version.strip()}".lower(),
            category="software",
            severity="high" if spike["level"] <= 2 and len(item["agents"]) >= 5 else "medium",
            title=f"{_event_name(spike)} rose after {verb} {change['name']}{version}",
            devices=devices, agent_ids=item["agents"],
            summary=(f"{len(item['agents'])} of the {spiking} devices where {_event_name(spike)} spiked had "
                     f"{change['name']} {change['change_type']}{(' to' + version) if change['change_type'] == 'updated' else ''} "
                     "shortly before. The change is a likely cause."),
            recommendation=(f"Check {change['name']}{version} on an affected device: read the event messages, the "
                            "vendor's release notes and known issues. If it is the cause, pause the rollout or "
                            "roll back while it is fixed."),
            evidence=[f"Example message: {spike['sample']}" if spike.get("sample") else ""] + [
                f"{devices.label(agent_id)}" for agent_id in item["agents"][:10]
            ],
        ))
    return findings


def security_findings(events: Sequence[Mapping[str, Any]], devices: Devices, *, now: datetime) -> list[dict[str, Any]]:
    findings = []
    since = now - CURRENT_WINDOW

    for group in group_lockouts(events, since=since):
        if group["count"] < LOCKOUT_MIN:
            continue
        caller_agents = []
        evidence = []
        for caller, count in group["callers"].most_common(8):
            match = devices.find_by_name(caller, group["company_id"]) if caller != "unknown" else None
            if match:
                caller_agents.append(match["agent_id"])
            evidence.append(f"Caller {caller}: {_plural(count, 'lockout')}" + (" (managed device)" if match else ""))
        evidence += [f"Logged by {devices.label(agent_id)}" for agent_id in sorted(group["agents"])[:4]]
        callers = [caller for caller in group["callers"] if caller != "unknown"]
        findings.append(_finding(
            f"lockout:{group['company_id']}:{group['account'].lower()}",
            category="security", severity="high" if group["count"] >= 10 else "medium",
            title=f"{group['account']} locked out {group['count']} times in 24 hours",
            devices=devices, agent_ids=list(group["agents"]) + caller_agents, company_ids=[group["company_id"]],
            summary=(f"The account {group['account']} was locked out {group['count']} times"
                     + (f", caused by {', '.join(callers[:3])}" if callers else "") + "."),
            recommendation=("On the caller computers, look for saved credentials, mapped drives, scheduled tasks, "
                            "services or phones still using an old password. Callers you do not recognise may mean "
                            "password guessing; check sign-in logs."),
            evidence=evidence,
        ))

    for cluster in cluster_accounts(events):
        if len(cluster["companies"]) < 2 and len(cluster["agents"]) < 3:
            continue
        names = sorted(cluster["names"], key=str.lower)
        cross = len(cluster["companies"]) >= 2
        findings.append(_finding(
            f"accounts:{cluster['base']}",
            category="security", severity="high" if cross else "medium",
            title=(f"Similar new account{'s' if len(names) > 1 else ''} {', '.join(names[:3])} on "
                   f"{_plural(len(cluster['agents']), 'device')}"
                   + (f" in {len(cluster['companies'])} companies" if cross else "")),
            devices=devices, agent_ids=cluster["agents"], company_ids=cluster["companies"],
            summary=(f"User accounts named {', '.join(names[:6])} were created on {_plural(len(cluster['agents']), 'device')}"
                     f"{' across ' + str(len(cluster['companies'])) + ' companies' if cross else ''}. "
                     "The same account appearing in several places is expected for an MSP's own admin account "
                     "and a warning sign otherwise."),
            recommendation=("Confirm who created the accounts and why. If nobody expected them, disable them, reset "
                            "the credentials of whoever created them and look for other persistence."),
            evidence=([f"Created by {', '.join(sorted(cluster['creators'])[:4])}"] if cluster["creators"] else [])
            + [devices.label(agent_id) for agent_id in sorted(cluster["agents"])[:8]],
        ))

    for group in group_services(events):
        many = len(group["agents"]) >= 3 or len(group["companies"]) >= 2
        if not group["suspicious"] and not many:
            continue
        severity = "high" if group["suspicious"] else ("low" if len(group["companies"]) >= 2 else "info")
        findings.append(_finding(
            f"service:{group['service'].lower()}",
            category="security", severity=severity,
            title=(f"Service {group['service']} installed from an unusual location" if group["suspicious"]
                   else f"New service {group['service']} on {_plural(len(group['agents']), 'device')}"),
            devices=devices, agent_ids=group["agents"], company_ids=group["companies"],
            summary=(f"The service {group['service']} was installed on {_plural(len(group['agents']), 'device')} in "
                     f"{_plural(len(group['companies']), 'company', 'companies')}."
                     + (" It runs from a temporary, user or script location, which attackers often use."
                        if group["suspicious"] else "")),
            recommendation=("Check that the service belongs to software you deployed. If not, stop and disable it, "
                            "keep a copy of its file for analysis and isolate the device."
                            if group["suspicious"] else
                            "Confirm the service came from a planned deployment."),
            evidence=[f"Runs: {path}" for path in sorted(group["paths"])[:3]]
            + [devices.label(agent_id) for agent_id in sorted(group["agents"])[:6]],
        ))

    for group in group_admin_adds(events):
        if len(group["agents"]) < 3 and len(group["companies"]) < 2:
            continue
        findings.append(_finding(
            f"admin-add:{group['member'].lower()}",
            category="security", severity="medium",
            title=f"{group['member']} added to admin groups on {_plural(len(group['agents']), 'device')}",
            devices=devices, agent_ids=group["agents"], company_ids=group["companies"],
            summary=(f"{group['member']} was added to {', '.join(sorted(g for g in group['groups'] if g)[:3]) or 'admin groups'} "
                     f"on {_plural(len(group['agents']), 'device')} in {_plural(len(group['companies']), 'company', 'companies')}."),
            recommendation="Confirm the change was planned; remove the membership where it was not.",
            evidence=[devices.label(agent_id) for agent_id in sorted(group["agents"])[:8]],
        ))

    cleared: dict[Any, set[int]] = defaultdict(set)
    for event in events:
        if event["kind"] == "log_cleared":
            cleared[event.get("company_id")].add(event["agent_id"])
    for company_id, agents in cleared.items():
        findings.append(_finding(
            f"log-cleared:{company_id}",
            category="security", severity="high",
            title=f"Event log cleared on {_plural(len(agents), 'device')}",
            devices=devices, agent_ids=agents, company_ids=[company_id] if company_id else [],
            summary=("A Windows event log was cleared. Attackers clear logs to hide what they did; it is rarely "
                     "part of normal work."),
            recommendation=("Ask who cleared it. If nobody did on purpose, treat the device as possibly compromised: "
                            "review sign-ins, new accounts and services around that time."),
            evidence=[devices.label(agent_id) for agent_id in sorted(agents)[:8]],
        ))
    return findings


def software_findings(changes: Sequence[Mapping[str, Any]], devices: Devices) -> list[dict[str, Any]]:
    findings = []
    for change in changes:
        agents = list(change["agents"])
        name = str(change["name"])
        if change["change_type"] == "removed" and _SECURITY_SOFTWARE.search(name):
            findings.append(_finding(
                f"security-removed:{name.lower()}",
                category="security", severity="high",
                title=f"{name} removed from {_plural(len(agents), 'device')}",
                devices=devices, agent_ids=agents,
                summary=f"Security software {name} is no longer installed on {_plural(len(agents), 'device')}.",
                recommendation=("Confirm it was replaced on purpose. If not, reinstall it and check the devices for "
                                "signs of tampering."),
                evidence=[devices.label(agent_id) for agent_id in agents[:10]],
            ))
        elif change["change_type"] == "installed" and _REMOTE_ACCESS_SOFTWARE.search(name):
            findings.append(_finding(
                f"remote-access:{name.lower()}",
                category="security", severity="medium",
                title=f"Remote access tool {name} installed on {_plural(len(agents), 'device')}",
                devices=devices, agent_ids=agents,
                summary=(f"{name} was installed on {_plural(len(agents), 'device')}. Remote access tools are used "
                         "by support vendors and by scammers and attackers."),
                recommendation="Confirm who installed it and why; remove it where it is not approved.",
                evidence=[devices.label(agent_id) for agent_id in agents[:10]],
            ))
        elif change["change_type"] in ("installed", "removed") and len(agents) >= 5 and len(change["companies"]) >= 2:
            verb = "installed on" if change["change_type"] == "installed" else "removed from"
            findings.append(_finding(
                f"software-{change['change_type']}:{name.lower()}",
                category="software", severity="info",
                title=f"{name} {verb} {_plural(len(agents), 'device')}",
                devices=devices, agent_ids=agents,
                summary=(f"{name} was {verb} {_plural(len(agents), 'device')} in "
                         f"{len(change['companies'])} companies in the last few days."),
                recommendation="Check this matches a planned rollout.",
                evidence=[devices.label(agent_id) for agent_id in agents[:10]],
            ))
    return findings


def performance_findings(outliers: Sequence[Mapping[str, Any]], devices: Devices) -> list[dict[str, Any]]:
    findings = []
    for outlier in outliers:
        cpu, ram, os_name = outlier["group"]
        label = "CPU" if outlier["metric"] == "cpu_percent" else "memory"
        spec = f"{cpu}, {ram} GB"
        hot = sorted(outlier["devices"].items(), key=lambda item: item[1], reverse=True)
        worst = hot[0][1]
        findings.append(_finding(
            f"performance:{outlier['metric']}:{_slug(spec + ':' + os_name)}",
            category="performance", severity="medium" if worst >= 85 else "low",
            title=f"{_plural(len(hot), 'device')} using far more {label} than similar devices",
            devices=devices, agent_ids=[agent_id for agent_id, _ in hot],
            summary=(f"Over the last few days these devices averaged up to {worst:.0f}% {label} use, against "
                     f"{outlier['median']:.0f}% for {outlier['peers']} devices with the same processor and memory "
                     f"({spec})."),
            recommendation=(f"Look at what is using {label} on these devices (startup programs, stuck updates, "
                            "security scans, failing disks). Users on them are likely to notice slowness."),
            evidence=[f"{devices.label(agent_id)}: {value:.0f}% average" for agent_id, value in hot[:8]],
        ))
    return findings


def health_findings(signals: Mapping[str, Sequence[Mapping[str, Any]]], failing_checks: Sequence[Mapping[str, Any]],
                    devices: Devices) -> list[dict[str, Any]]:
    findings = []
    low_disk = signals.get("low_disk") or []
    if low_disk:
        critical = [item for item in low_disk if item["free_bytes"] < CRITICAL_DISK_BYTES]
        findings.append(_finding(
            "health:low-disk", category="health", severity="medium" if critical else "low",
            title=f"{_plural(len(low_disk), 'device')} nearly out of space on the system drive",
            devices=devices, agent_ids=[item["agent_id"] for item in low_disk],
            summary=(f"{_plural(len(low_disk), 'device')} {'has' if len(low_disk) == 1 else 'have'} less than "
                     f"{_gb(LOW_DISK_BYTES)} free on the system drive"
                     + (f", {len(critical)} under {_gb(CRITICAL_DISK_BYTES)}." if critical else ".")),
            recommendation="Free space before updates start failing: clear temporary files and old updates, or add storage.",
            evidence=[f"{devices.label(item['agent_id'])}: {_gb(item['free_bytes'])} free" for item in low_disk[:10]],
        ))
    long_uptime = signals.get("long_uptime") or []
    if long_uptime:
        findings.append(_finding(
            "health:long-uptime", category="health", severity="low",
            title=f"{_plural(len(long_uptime), 'device')} not restarted in over {LONG_UPTIME_DAYS} days",
            devices=devices, agent_ids=[item["agent_id"] for item in long_uptime],
            summary="Devices that never restart do not finish installing updates and tend to slow down.",
            recommendation="Schedule a restart, or check why updates are not restarting them.",
            evidence=[f"{devices.label(item['agent_id'])}: up {item['days']} days" for item in long_uptime[:10]],
        ))
    stale = signals.get("stale_agents") or []
    if stale:
        findings.append(_finding(
            "health:stale-agents", category="health", severity="low",
            title=f"{_plural(len(stale), 'RMM agent')} silent for over {STALE_AGENT_DAYS} days",
            devices=devices, agent_ids=[item["agent_id"] for item in stale],
            summary="These devices' RMM agents have not checked in for two weeks, so they are not being monitored.",
            recommendation="Check whether the devices were retired (archive them) or the agent needs reinstalling.",
            evidence=[f"{devices.label(item['agent_id'])}: last seen {item['days']} days ago" for item in stale[:10]],
        ))
    for check in failing_checks:
        failing, total = int(check.get("failing") or 0), int(check.get("devices") or 0)
        if total < 4 or failing / total < 0.25:
            continue
        findings.append(_finding(
            f"check:{check['id']}", category="health", severity="medium",
            title=f"Check {check['name']} failing on {failing} of {total} devices",
            devices=devices, agent_ids=check.get("agents") or [],
            summary=("A quarter or more of the devices this check covers are failing it, which points to a "
                     "shared cause or a threshold that needs adjusting."),
            recommendation="Compare the failing devices for a common cause before working on them one by one.",
            evidence=[devices.label(agent_id) for agent_id in (check.get("agents") or [])[:10]],
        ))
    return findings


# --------------------------------------------------------------------------- #
# AI assessment
# --------------------------------------------------------------------------- #


def build_llm_config() -> dict[str, str]:
    """``FINDINGS_LLM_*`` when any is set, otherwise the troubleshooter's LLM."""

    settings = get_settings()
    base_url = str(settings.findings_llm_base_url or "").strip()
    model = str(settings.findings_llm_model or "").strip()
    api_key = str(settings.findings_llm_api_key or "").strip()
    if not (base_url or model or api_key):
        return tray_service.build_troubleshoot_llm_config()
    return {"base_url": (base_url or "http://127.0.0.1:11434").rstrip("/"), "model": model or "llama3",
            "api_key": api_key}


_AI_INSTRUCTIONS = """You are a senior engineer at a managed service provider (MSP) reviewing remote monitoring
(RMM) data gathered from every customer company's computers. Give technicians a best-guess assessment of
abnormalities worth their time: problems starting, security concerns, and patterns across devices or companies.

Look especially for links between signals, for example an event spike that began on the devices where a
program was just updated; account lockouts and the computers causing them; similar new user accounts on
many devices or companies; services installed across many devices; devices slower than identical devices;
and anything else that looks out of place. Rule findings already listed in "rule_findings" are shown to
technicians, so only add a finding about the same thing when you can explain more (a cause, a link or a
better next step).

Respond with one JSON object and no other text:
{"findings": [{"key": str, "title": str, "category": str, "severity": str, "confidence": int,
  "summary": str, "evidence": [str], "recommendation": str, "device_ids": [int], "company_ids": [int]}]}

Rules:
- At most 10 findings, most important first. Return {"findings": []} when nothing stands out.
- "category" is one of: security, events, software, performance, health, other.
- "severity" is one of: critical, high, medium, low, info. Use critical only for likely active compromise
  or outage across many devices.
- "confidence" is 0-100: how sure you are that this needs attention.
- "key" is a short stable identifier made of lower-case words and dashes describing the issue (for example
  "outlook-crashes-after-office-update"). When an issue is the same as one in "existing_ai_findings", reuse
  its key.
- "device_ids" and "company_ids" may only contain ids from the "devices" and "companies" lists.
- Write plain English for technicians, no markdown. Keep "summary" and "recommendation" under 600
  characters, and give up to 6 short "evidence" lines quoting the numbers you relied on.
- Base everything on the data; say what is uncertain rather than inventing details."""


def _device_ref(devices: Devices, agent_id: Any) -> dict[str, Any] | None:
    device = devices.get(agent_id)
    if not device:
        return None
    ram = _ram_bucket(device.get("ram_bytes"))
    return {
        "device_id": int(device["agent_id"]), "name": device["name"], "company_id": device.get("company_id"),
        "os": _clip(device.get("os_name") or device.get("os"), 60) or None,
        "model": _clip(" ".join(filter(None, [device.get("manufacturer"), device.get("model")])), 80) or None,
        "cpu": _clip(device.get("cpu_model"), 80) or None, "ram_gb": ram or None,
    }


def build_signals(
    *,
    devices: Devices,
    spikes: Sequence[Mapping[str, Any]],
    security_events: Sequence[Mapping[str, Any]],
    software_changes: Sequence[Mapping[str, Any]],
    outliers: Sequence[Mapping[str, Any]],
    health: Mapping[str, Sequence[Mapping[str, Any]]],
    failing_checks: Sequence[Mapping[str, Any]],
    rule_findings: Sequence[Mapping[str, Any]],
    existing_ai: Sequence[Mapping[str, Any]],
    now: datetime,
    baseline_days: int,
) -> dict[str, Any]:
    """The compact, bounded summary of every signal sent to the model."""

    referenced: set[int] = set()

    def refs(agent_ids: Iterable[Any], limit: int = 12) -> list[int]:
        chosen = [int(agent_id) for agent_id in list(agent_ids)[:limit] if devices.get(agent_id)]
        referenced.update(chosen)
        return chosen

    lockouts = [{
        "account": group["account"], "company_id": group["company_id"], "count": group["count"],
        "callers": dict(group["callers"].most_common(6)), "logged_on": refs(group["agents"], 4),
    } for group in group_lockouts(security_events, since=now - CURRENT_WINDOW)[:15]]
    accounts = [{
        "names": sorted(cluster["names"])[:8], "devices": refs(cluster["agents"]), "company_ids": sorted(cluster["companies"]),
        "created_by": sorted(cluster["creators"])[:4],
    } for cluster in cluster_accounts(security_events)[:15]]
    services = [{
        "service": group["service"], "paths": sorted(group["paths"])[:3], "devices": refs(group["agents"]),
        "company_ids": sorted(group["companies"]), "unusual_location": group["suspicious"],
    } for group in group_services(security_events)[:20]]
    admin_adds = [{
        "member": group["member"], "groups": sorted(group["groups"])[:3], "devices": refs(group["agents"]),
        "company_ids": sorted(group["companies"]),
    } for group in group_admin_adds(security_events)[:10]]
    cleared = sorted({event["agent_id"] for event in security_events if event["kind"] == "log_cleared"})

    signals = {
        "window": {"current": "last 24 hours", "baseline_days": baseline_days, "generated_at": now.isoformat()},
        "platform": {"devices": len(devices), "companies": len(devices.companies())},
        "event_spikes": [{
            "log": spike["log_name"], "source": spike["provider"], "event_id": spike["event_id"],
            "level": {1: "critical", 2: "error", 3: "warning"}.get(spike["level"], "information"),
            "last_24h": spike["total"], "baseline_per_day": spike["baseline_daily"], "new": spike["is_new"],
            "device_count": spike["devices"], "company_count": spike["companies"], "sample_message": spike.get("sample"),
            "top_devices": [{"device_id": d["agent_id"], "last_24h": d["current"], "baseline_per_day": d["baseline_daily"]}
                            for d in spike["spiking_devices"][:8] if refs([d["agent_id"]])],
        } for spike in spikes],
        "account_lockouts": lockouts,
        "new_accounts": accounts,
        "new_services": services,
        "admin_group_additions": admin_adds,
        "event_log_cleared_on": refs(cleared),
        "software_changes": [{
            "name": change["name"], "change": change["change_type"], "new_version": change.get("new_version"),
            "old_versions": sorted(change["old_versions"])[:3], "device_count": len(change["agents"]),
            "company_count": len(change["companies"]), "devices": refs(change["agents"], 8),
        } for change in software_changes[:40]],
        "performance_outliers": [{
            "metric": outlier["metric"], "spec": {"cpu": outlier["group"][0], "ram_gb": outlier["group"][1]},
            "peer_median_percent": round(outlier["median"], 1), "peers": outlier["peers"],
            "devices": [{"device_id": agent_id, "average_percent": round(value, 1)}
                        for agent_id, value in sorted(outlier["devices"].items(), key=lambda i: -i[1])[:8]
                        if refs([agent_id])],
        } for outlier in outliers[:10]],
        "low_disk": [{"device_id": item["agent_id"], "free_gb": round(item["free_bytes"] / 1024 ** 3, 1)}
                     for item in (health.get("low_disk") or [])[:15] if refs([item["agent_id"]])],
        "long_uptime_device_count": len(health.get("long_uptime") or []),
        "silent_agent_count": len(health.get("stale_agents") or []),
        "failing_checks": [{"name": check["name"], "type": check["check_type"], "failing": int(check["failing"]),
                            "of_devices": int(check["devices"])} for check in failing_checks[:15]],
        "rule_findings": [{"key": f["key"], "title": f["title"], "severity": f["severity"]} for f in rule_findings[:40]],
        "existing_ai_findings": [{"key": row["finding_key"], "title": row["title"]} for row in existing_ai[:30]],
    }
    signals["devices"] = [ref for ref in (_device_ref(devices, agent_id) for agent_id in sorted(referenced)) if ref]
    company_names = {}
    for device in devices.by_agent.values():
        if device.get("company_id"):
            company_names[int(device["company_id"])] = device.get("company_name") or f"Company {device['company_id']}"
    signals["companies"] = [{"company_id": company_id, "name": name} for company_id, name in sorted(company_names.items())
                            if any(ref.get("company_id") == company_id for ref in signals["devices"])]
    return signals


def has_signals(signals: Mapping[str, Any]) -> bool:
    keys = ("event_spikes", "account_lockouts", "new_accounts", "new_services", "admin_group_additions",
            "event_log_cleared_on", "software_changes", "performance_outliers", "low_disk", "failing_checks")
    return any(signals.get(key) for key in keys)


def _response_text(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, list):
                return "".join(str(part.get("text") or "") if isinstance(part, Mapping) else str(part)
                               for part in content).strip()
            return str(content or "").strip()
        return str(choices[0].get("text") or "").strip()
    message = payload.get("message")
    if isinstance(message, Mapping):
        return str(message.get("content") or "").strip()
    return ""


async def _chat(llm: Mapping[str, str], messages: list[dict[str, str]]) -> str:
    """One request to the findings model's OpenAI-compatible chat endpoint."""

    base_url = str(llm.get("base_url") or "").rstrip("/")
    model = str(llm.get("model") or "")
    body = {"model": model, "messages": messages, "stream": False, "response_format": {"type": "json_object"}}
    headers = {"Content-Type": "application/json"}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"
    prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
    started = time.monotonic()
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base_url}/v1/chat/completions", json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
        text = _response_text(payload)[:_LLM_RESPONSE_LIMIT_CHARS]
    except Exception:
        await llm_usage.record_usage(
            feature=LLM_FEATURE, provider="openai-compatible", model=model, status="failed", usage=(0, 0),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    await llm_usage.record_usage(
        feature=LLM_FEATURE, provider="openai-compatible", model=model, status="succeeded",
        usage=llm_usage.extract_token_usage(payload), prompt=prompt_text, response_text=text,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if not text:
        raise ValueError("The AI model returned an empty response")
    return text


def _json_object(text: str) -> Mapping[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        return ai_prompt_security.parse_json_object(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        return ai_prompt_security.parse_json_object(cleaned[start:end + 1])


def parse_ai_findings(text: str, devices: Devices, signals: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validated findings from the model's reply. Devices and companies not
    in the signals are dropped; malformed items are skipped."""

    data = _json_object(text)
    items = data.get("findings")
    if not isinstance(items, list):
        raise ValueError("AI output has no findings list")
    known_devices = {int(ref["device_id"]) for ref in signals.get("devices") or []}
    known_companies = {int(ref["company_id"]) for ref in signals.get("companies") or []}
    for item in signals.get("account_lockouts") or []:
        if item.get("company_id"):
            known_companies.add(int(item["company_id"]))

    def ints(value: Any, allowed: set[int]) -> list[int]:
        if not isinstance(value, list):
            return []
        return [int(v) for v in value if isinstance(v, int) and not isinstance(v, bool) and int(v) in allowed]

    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items[:MAX_AI_FINDINGS]:
        if not isinstance(item, Mapping):
            continue
        title = _clip(item.get("title"), 160)
        summary = str(item.get("summary") or "").strip()[:1200]
        if len(title) < 3 or not summary:
            continue
        key = _slug(str(item.get("key") or "")) or _slug(title)
        if key in seen:
            continue
        seen.add(key)
        category = str(item.get("category") or "").lower()
        severity = str(item.get("severity") or "").lower()
        try:
            confidence = max(0, min(100, int(item.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        agent_ids = ints(item.get("device_ids"), known_devices)
        finding = _finding(
            key, source="ai", devices=devices, agent_ids=agent_ids,
            company_ids=ints(item.get("company_ids"), known_companies),
            category=category if category in CATEGORIES else "other",
            severity=severity if severity in SEVERITIES else "info",
            title=title, summary=summary, confidence=confidence,
            recommendation=str(item.get("recommendation") or "").strip()[:1200],
            evidence=[str(line) for line in evidence[:6] if isinstance(line, (str, int, float))],
        )
        findings.append(finding)
    return findings


async def ai_findings(signals: Mapping[str, Any], devices: Devices) -> tuple[list[dict[str, Any]], str]:
    llm = build_llm_config()
    record = ai_prompt_security.UntrustedRecord(
        record_id="rmm-signals",
        provenance="Telemetry from RMM agents on customer devices, aggregated by MyPortal",
        content=signals,
        allowed_use="Find abnormalities for technicians to review",
    )
    messages = ai_prompt_security.build_messages(
        _AI_INSTRUCTIONS, [record], task="Assess the RMM signals and return the findings JSON object."
    )
    text = await _chat(llm, messages)
    return parse_ai_findings(text, devices, signals), llm["model"]


# --------------------------------------------------------------------------- #
# Running an analysis
# --------------------------------------------------------------------------- #


async def _secondary_ready(database: Any) -> bool:
    try:
        return bool(await database.ensure_connected())
    except Exception as exc:  # pragma: no cover - logged, analysis continues without it
        log_warning("RMM findings could not reach a secondary database", error=str(exc))
        return False


async def analyse(run_id: int) -> dict[str, Any]:
    """Gather signals, store findings and return the run's outcome."""

    settings = get_settings()
    now = _utcnow()
    baseline_days = int(settings.findings_baseline_days)
    lookback_start = now - CURRENT_WINDOW - timedelta(days=baseline_days)
    problems: list[str] = []

    devices = Devices(await repo.list_devices())
    events_ready = await _secondary_ready(event_log_db)
    stats_ready = await _secondary_ready(stats_db)

    spikes: list[dict[str, Any]] = []
    security_events: list[dict[str, Any]] = []
    if events_ready:
        try:
            spikes = await collect_event_spikes(devices, now=now, baseline_days=baseline_days)
            security_events = await collect_security_events(now=now, baseline_days=baseline_days)
        except Exception as exc:
            events_ready = False
            problems.append("Event logs could not be read.")
            log_warning("RMM findings could not read event logs", error=str(exc))
    else:
        problems.append("The event log database is not connected, so event logs were not analysed.")

    changes_recorded = await record_software_changes(devices, now=now, since=lookback_start)
    software_changes = group_software_changes(await repo.list_software_changes(lookback_start), devices)

    outliers: list[dict[str, Any]] = []
    if stats_ready:
        try:
            outliers = find_performance_outliers(
                await repo.stat_averages(metrics=("cpu_percent", "memory_used_percent"),
                                         since=now - timedelta(days=baseline_days)), devices)
        except Exception as exc:
            stats_ready = False
            problems.append("Device stats could not be read.")
            log_warning("RMM findings could not read device stats", error=str(exc))
    else:
        problems.append("The stats database is not connected, so performance was not compared.")

    health = health_signals(devices, now=now)
    failing = [dict(check) for check in await repo.failing_checks()]
    for check in failing:
        check["agents"] = await repo.list_failing_agents(int(check["id"]))

    rule_findings = (
        spike_findings(spikes, devices, baseline_days=baseline_days)
        + correlation_findings(correlate(spikes, software_changes), devices)
        + security_findings(security_events, devices, now=now)
        + software_findings(software_changes, devices)
        + performance_findings(outliers, devices)
        + health_findings(health, failing, devices)
    )

    ai_status, ai_model, ai_error = "off", None, None
    found_by_ai: list[dict[str, Any]] = []
    if settings.findings_ai_enabled:
        signals = build_signals(
            devices=devices, spikes=spikes, security_events=security_events, software_changes=software_changes,
            outliers=outliers, health=health, failing_checks=failing, rule_findings=rule_findings,
            existing_ai=await repo.list_active_keys("ai"), now=now, baseline_days=baseline_days,
        )
        if not has_signals(signals):
            ai_status = "skipped"
        else:
            try:
                found_by_ai, ai_model = await ai_findings(signals, devices)
                ai_status = "used"
            except Exception as exc:
                ai_status, ai_error = "failed", _clip(str(exc) or exc.__class__.__name__, 500)
                log_warning("RMM findings AI assessment failed", error=ai_error)

    for finding in rule_findings + found_by_ai:
        await repo.upsert_finding(finding, run_id=run_id)
    # A failed AI assessment keeps the previous AI findings; missing data sources keep theirs.
    await repo.deactivate_unseen(run_id, sources=["rule", "ai"] if ai_status != "failed" else ["rule"],
                                 keep_key_prefixes=_kept_prefixes(events_ready, stats_ready))
    await repo.prune(findings_before=now - timedelta(days=90), runs_before=now - timedelta(days=90),
                     changes_before=now - timedelta(days=30))
    return {
        "finding_count": len(rule_findings) + len(found_by_ai),
        "ai_status": ai_status, "ai_model": ai_model, "ai_error": ai_error,
        "stats": {
            "devices": len(devices), "companies": len(devices.companies()), "event_spikes": len(spikes),
            "security_events": len(security_events), "software_changes": changes_recorded,
            "software_change_groups": len(software_changes), "performance_outliers": len(outliers),
            "rule_findings": len(rule_findings), "ai_findings": len(found_by_ai), "problems": problems,
            "event_logs": events_ready, "device_stats": stats_ready,
        },
    }


def _kept_prefixes(events_ready: bool, stats_ready: bool) -> list[str]:
    """Key prefixes of rule findings whose data was unavailable this time."""

    prefixes = []
    if not events_ready:
        prefixes += ["spike:", "correlation:", "lockout:", "accounts:", "service:", "admin-add:", "log-cleared:"]
    if not stats_ready:
        prefixes.append("performance:")
    return prefixes


async def _execute(run_id: int) -> None:
    try:
        outcome = await analyse(run_id)
        await repo.finish_run(run_id, status="succeeded", error=None, **outcome)
        log_info("RMM findings analysis finished", run_id=run_id, findings=outcome["finding_count"],
                 ai_status=outcome["ai_status"])
    except Exception as exc:
        log_warning("RMM findings analysis failed", run_id=run_id, error=str(exc))
        await repo.finish_run(run_id, status="failed", ai_status="off", ai_model=None, ai_error=None,
                              error=_clip(str(exc) or exc.__class__.__name__, 500), finding_count=0, stats={})
    finally:
        try:
            await singleton_jobs.release(LEASE_NAME)
        except Exception:  # pragma: no cover - the lease expires on its own
            pass


class AnalysisRunning(Exception):
    """An analysis is already running."""


async def start_analysis(*, trigger_source: str, user_id: int | None = None, wait: bool = False) -> int:
    """Start an analysis in the background (or wait for it) and return its run ID."""

    await repo.fail_stale_runs(_utcnow() - timedelta(minutes=STALE_RUN_MINUTES))
    if await repo.get_running_run():
        raise AnalysisRunning("An analysis is already running.")
    if not await singleton_jobs.acquire(LEASE_NAME, LEASE_SECONDS):
        raise AnalysisRunning("An analysis is already running on another server.")
    run_id = await repo.create_run(trigger_source=trigger_source, requested_by_user_id=user_id)
    if wait:
        await _execute(run_id)
        return run_id
    task = asyncio.create_task(_execute(run_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return run_id


async def tick() -> None:
    """Start a scheduled analysis when one is due (on the RMM automation tick)."""

    settings = get_settings()
    if not settings.findings_enabled:
        return
    # Shares the tick with RMM automation, so a problem here must never stop it.
    try:
        latest = await repo.get_latest_run()
        started = _as_datetime(latest.get("started_at")) if latest else None
        if started and started > _utcnow() - timedelta(hours=int(settings.findings_interval_hours)):
            return
        await start_analysis(trigger_source="schedule")
    except AnalysisRunning:
        return
    except Exception as exc:
        log_warning("RMM findings could not start a scheduled analysis", error=str(exc))


# --------------------------------------------------------------------------- #
# Presenting findings
# --------------------------------------------------------------------------- #


SEVERITY_VARIANTS = {"critical": "error", "high": "partial_outage", "medium": "warning", "low": "info", "info": "neutral"}


def present(finding: Mapping[str, Any], names: Mapping[str, Mapping[int, Any]]) -> dict[str, Any]:
    """A finding as the page and API show it."""

    assets = names.get("assets") or {}
    companies = names.get("companies") or {}
    return {
        "id": int(finding["id"]),
        "key": finding.get("finding_key"),
        "source": finding.get("source"),
        "source_label": "AI insight" if finding.get("source") == "ai" else "Rule",
        "category": finding.get("category"),
        "category_label": CATEGORIES.get(str(finding.get("category")), "Other"),
        "severity": finding.get("severity"),
        "severity_label": SEVERITIES.get(str(finding.get("severity")), "Info"),
        "severity_variant": SEVERITY_VARIANTS.get(str(finding.get("severity")), "neutral"),
        "severity_rank": SEVERITY_RANK.get(str(finding.get("severity")), len(SEVERITIES)),
        "title": finding.get("title") or "",
        "summary": finding.get("summary") or "",
        "recommendation": finding.get("recommendation") or "",
        "evidence": list(finding.get("evidence") or []),
        "confidence": finding.get("confidence"),
        "status": finding.get("status") or "open",
        "status_label": STATUSES.get(str(finding.get("status")), "Open"),
        "status_note": finding.get("status_note") or "",
        "status_changed_at": _iso(finding.get("status_changed_at")),
        "is_active": bool(finding.get("is_active")),
        "occurrences": int(finding.get("occurrences") or 1),
        "first_seen_at": _iso(finding.get("first_seen_at")),
        "last_seen_at": _iso(finding.get("last_seen_at")),
        "devices": [{"asset_id": asset_id, "name": (assets.get(asset_id) or {}).get("name") or f"Asset {asset_id}",
                     "company": companies.get((assets.get(asset_id) or {}).get("company_id"))}
                    for asset_id in finding.get("asset_ids") or []],
        "companies": [{"id": company_id, "name": companies.get(company_id) or f"Company {company_id}"}
                      for company_id in finding.get("company_ids") or []],
    }


def run_summary(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    started, finished = _as_datetime(run.get("started_at")), _as_datetime(run.get("finished_at"))
    return {
        "id": int(run["id"]),
        "status": run.get("status"),
        "trigger": run.get("trigger_source"),
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "duration_seconds": int((finished - started).total_seconds()) if started and finished else None,
        "ai_status": run.get("ai_status"),
        "ai_model": run.get("ai_model"),
        "ai_error": run.get("ai_error"),
        "error": run.get("error"),
        "finding_count": int(run.get("finding_count") or 0),
        "stats": run.get("stats") or {},
    }
