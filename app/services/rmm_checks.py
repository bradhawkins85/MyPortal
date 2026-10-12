"""RMM checks: watch disk space, ping targets, CPU load, free memory, Windows
services, network throughput, script results and agent check-ins.

A check applies to every company (super admins only), one company or one
device, like an event log rule. For the same thing on a device (its
:func:`override_key`: the type and target, such as disk ``C:``) the device's
own checks replace its company's, which replace those for every company. A
replacing check that is off switches that thing off for its scope
(:func:`effective_checks`).

How each type is checked:

* **Disk space, ping, CPU, memory, service and network** checks are measured
  by the device's RMM agent. It asks which to collect (:func:`agent_config`)
  and reports every :data:`REPORT_INTERVAL_SECONDS` (:func:`ingest_report`).
  A service check can have the agent start, restart or stop the service
  itself when it is not in the expected state.
* **Script** checks queue a Gitea script on the device every
  ``interval_minutes`` and compare its exit code or output with what is
  expected when it finishes (:func:`run_due_script_checks`, :func:`run_finished`).
* **Agent offline** checks compare the agent's last check-in with
  ``duration_minutes`` on the server (:func:`evaluate_offline`).

A problem must last ``duration_minutes`` before the check fails (status
``warning`` until then). When a check starts failing it takes the same actions
as an event log rule (:mod:`app.services.rmm_actions`): run a script on the
device (again each ``cooldown_minutes`` while it keeps failing, or once when
the cooldown is 0), schedule a script for later (dropped if the check passes
before it runs; :func:`run_due_scheduled_checks`) or raise a ticket for the
device. While that ticket is open later failures and the recovery are added to
it as internal updates.

While a device has disk, ping, CPU, memory or network checks, the values it
reports are kept in the separate stats database (:mod:`app.core.stats_database`)
for the asset page's charts.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from loguru import logger

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.core.stats_database import stats_db
from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_checks as repo
from app.repositories import rmm_stats as stats_repo
from app.repositories import rmm_targeting as targeting_repo
from app.security.encryption import encrypt_secret
from app.services import rmm_actions, rmm_automation, rmm_event_log_rules, rmm_scripts, rmm_targeting
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "check"
CHECK_TYPES = {
    "disk": "Disk space",
    "ping": "Ping",
    "cpu": "CPU load",
    "memory": "Free memory",
    "service": "Windows service",
    "network": "Network throughput",
    "script": "Script result",
    "offline": "Agent offline",
}
AGENT_TYPES = ("disk", "ping", "cpu", "memory", "service", "network")
STATS_TYPES = ("disk", "ping", "cpu", "memory", "network")
ACTIONS = {
    "none": "Show the result only",
    "run_script": "Run a script",
    "schedule_script": "Schedule a script",
    "create_ticket": "Create a ticket",
}
SCRIPT_ACTIONS = ("run_script", "schedule_script")
STATUSES = {"pending": "Waiting", "passing": "Passing", "warning": "Problem", "failing": "Failing"}
REPORT_INTERVAL_SECONDS = 60
CONFIG_REFRESH_SECONDS = 300
DEFAULT_COOLDOWN_MINUTES = 60
MAX_COOLDOWN_MINUTES = 7 * 24 * 60
MAX_DURATION_MINUTES = 7 * 24 * 60
MIN_OFFLINE_MINUTES = 5
DEFAULT_SCRIPT_INTERVAL = 15
MIN_SCRIPT_INTERVAL = 5
MAX_SCRIPT_INTERVAL = 7 * 24 * 60
MAX_PINGS = 10
UNCHANGED_WRITE_INTERVAL = timedelta(minutes=10)
MAX_SERVICES = 50

DISK_METRICS = {"free_percent": "free space (%)", "free_gb": "free space (GB)"}
MEMORY_METRICS = {"free_percent": "free memory (%)", "free_gb": "free memory (GB)"}
COMPARISONS = {"below": "is below", "above": "is above"}
NETWORK_DIRECTIONS = {"in": "received", "out": "sent", "total": "received and sent"}
SERVICE_STATES = {"running": "Running", "stopped": "Stopped"}
SERVICE_FIXES = {
    "none": "Do nothing on the device",
    "start": "Start it",
    "restart": "Restart it",
    "stop": "Stop it",
}
SCRIPT_EXPECTATIONS = {
    "exit_code": "exit code is",
    "output_contains": "output contains",
    "output_not_contains": "output does not contain",
    "output_equals": "output is exactly",
}
CHECK_VARIABLES = (
    ("Check name", "{{check.name}}"),
    ("Check type", "{{check.type}}"),
    ("What it checks", "{{check.target}}"),
    ("Result", "{{check.result}}"),
    ("Device", "{{check.device}}"),
)

_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.:_-]{0,252})$")
_SERVICE = re.compile(r"^[A-Za-z0-9_.$@][A-Za-z0-9 _.$@()+-]{0,255}$")
_INTERFACE = re.compile(r"^[^\x00-\x1f]{0,255}$")
_DRIVE_LETTER = re.compile(r"^([A-Za-z]):?\\?$")
_MOUNT = re.compile(r"^/[^\x00-\x1f]{0,254}$")

GB = 1024 ** 3


class CheckError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- #
# Which checks reach a device
# --------------------------------------------------------------------------- #


def normalise_drive(value: Any) -> str:
    """``c``, ``C:`` and ``C:\\`` are all drive ``C:``; mount points stay as given."""

    text = str(value or "").strip()
    match = _DRIVE_LETTER.match(text)
    if match:
        return match.group(1).upper() + ":"
    if len(text) > 1:
        text = text.rstrip("/") or "/"
    return text


def override_key(check: Mapping[str, Any]) -> tuple[str, str]:
    return str(check.get("check_type")), str(check.get("target") or "").casefold()


def _specificity(check: Mapping[str, Any]) -> int:
    if check.get("asset_id") is not None:
        return 2
    return 1 if check.get("company_id") is not None else 0


def _in_scope(check: Mapping[str, Any], company_id: int | None, asset_id: int | None) -> bool:
    if check.get("asset_id") is not None:
        return asset_id is not None and int(check["asset_id"]) == int(asset_id)
    if check.get("company_id") is not None:
        return company_id is not None and int(check["company_id"]) == int(company_id)
    return True


def effective_checks(checks: Iterable[Mapping[str, Any]], company_id: int | None, asset_id: int | None,
                     *, include_off: bool = False) -> list[dict[str, Any]]:
    """The checks a device runs.

    For each :func:`override_key` only the checks from the most specific scope
    that has any count; when ``include_off`` is false those that are off are
    then left out. Checks from other companies or devices are ignored."""

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for check in checks:
        if not _in_scope(check, company_id, asset_id):
            continue
        groups[override_key(check)].append(dict(check))
    chosen: list[dict[str, Any]] = []
    for items in groups.values():
        level = max(_specificity(item) for item in items)
        for item in items:
            if _specificity(item) != level:
                continue
            if item.get("is_enabled") or include_off:
                chosen.append(item)
    return sorted(chosen, key=lambda item: int(item["id"]))


def replaced_by(check: Mapping[str, Any], checks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """More specific checks for the same thing that replace ``check`` (for the device or company)."""

    key = override_key(check)
    level = _specificity(check)
    return [dict(other) for other in checks if override_key(other) == key and _specificity(other) > level]


async def matching_checks_for_agents(
    checks: Iterable[Mapping[str, Any]], agents: Iterable[Mapping[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """The checks in each agent's scope whose target rules match its device,
    before scope overrides are resolved.

    Inventory is loaded in one batch for agents with valid scoped rule trees;
    legacy unrestricted checks require no additional inventory queries.
    """

    candidates = []
    matchers = {}
    for check in checks:
        row = dict(check)
        if row.get("target_rules") is not None:
            try:
                matchers[int(row["id"])] = rmm_targeting.compile_rules(row["target_rules"])
            except (ValueError, TypeError, OverflowError, RecursionError):
                continue  # corrupt rules may never become unrestricted
        candidates.append(row)
    devices = {int(agent["id"]): dict(agent) for agent in agents}
    scoped = {
        agent_id: [check for check in candidates if _in_scope(check, agent.get("company_id"), agent.get("asset_id"))]
        for agent_id, agent in devices.items()
    }
    rule_devices = [agent for agent_id, agent in devices.items()
                    if any(check.get("target_rules") is not None for check in scoped[agent_id])]
    contexts = await targeting_repo.contexts_for_agents(rule_devices) if rule_devices else {}
    return {
        agent_id: [check for check in scoped[agent_id]
                   if check.get("target_rules") is None or matchers[int(check["id"])](contexts.get(agent_id))]
        for agent_id in devices
    }


async def targeted_checks_for_agents(
    checks: Iterable[Mapping[str, Any]], agents: Iterable[Mapping[str, Any]], *, include_off: bool = False,
) -> dict[int, list[dict[str, Any]]]:
    """Select checks against current inventory before resolving scope overrides.

    A disabled override only switches off checks on devices its rules match.
    """

    devices = {int(agent["id"]): dict(agent) for agent in agents}
    matching = await matching_checks_for_agents(checks, devices.values())
    return {
        agent_id: effective_checks(matching[agent_id], agent.get("company_id"), agent.get("asset_id"),
                                   include_off=include_off)
        for agent_id, agent in devices.items()
    }


async def checks_for_agent(agent: Mapping[str, Any], *, with_secrets: bool = False,
                           include_off: bool = False) -> list[dict[str, Any]]:
    company_id = int(agent["company_id"]) if agent.get("company_id") is not None else None
    asset_id = int(agent["asset_id"]) if agent.get("asset_id") is not None else None
    checks = await repo.list_checks_for_device(company_id, asset_id, with_secrets=with_secrets)
    selected = await targeted_checks_for_agents(checks, [agent], include_off=include_off)
    return selected[int(agent["id"])]


# --------------------------------------------------------------------------- #
# Describing checks
# --------------------------------------------------------------------------- #


def describe(check: Mapping[str, Any]) -> str:
    """When the check fails, in words."""

    kind = check.get("check_type")
    settings = check.get("settings") or {}
    target = check.get("target") or ""
    duration = int(check.get("duration_minutes") or 0)
    lasting = f" for {duration} minute{'s' if duration != 1 else ''}" if duration and kind != "offline" else ""
    if kind == "disk":
        drive = "any drive" if target in ("", "*") else target
        percent = settings.get("metric") == "free_percent"
        limit = _round(float(settings.get("threshold") or 0)) + ("%" if percent else " GB")
        return f"Free space on {drive} {COMPARISONS.get(settings.get('operator'), 'is below')} {limit}{lasting}"
    if kind == "ping":
        text = f"Ping {target} loses {int(settings.get('max_loss_percent') or 100)}% or more of its replies"
        if settings.get("max_latency_ms"):
            text += f" or takes over {int(settings['max_latency_ms'])} ms"
        return text + lasting
    if kind == "cpu":
        return f"CPU is over {_round(float(settings.get('threshold') or 0))}%{lasting}"
    if kind == "memory":
        unit = "%" if settings.get("metric") == "free_percent" else " GB"
        return f"Free memory is below {_round(float(settings.get('threshold') or 0))}{unit}{lasting}"
    if kind == "service":
        expected = SERVICE_STATES.get(settings.get("expected"), "Running").lower()
        text = f"{target} is not {expected}{lasting}"
        fix = settings.get("fix") or "none"
        if fix in ("start", "restart", "stop"):
            text += f"; the agent will {fix} it"
        return text
    if kind == "network":
        where = f"{target}" if target else "All interfaces"
        return (f"{where}: data {NETWORK_DIRECTIONS.get(settings.get('direction'), 'received and sent')} "
                f"{COMPARISONS.get(settings.get('operator'), 'is above')} "
                f"{_round(float(settings.get('threshold_mbps') or 0))} Mbps{lasting}")
    if kind == "script":
        name = check.get("check_script_name") or "The script"
        expect = settings.get("expect") or "exit_code"
        value = settings.get("value") if settings.get("value") not in (None, "") else "0"
        every = int(settings.get("interval_minutes") or DEFAULT_SCRIPT_INTERVAL)
        return f"{name}, run every {every} min, fails unless its {SCRIPT_EXPECTATIONS.get(expect, expect)} “{value}”{lasting}"
    if kind == "offline":
        return f"The agent has not checked in for {duration} minute{'s' if duration != 1 else ''}"
    return str(kind)


def can_edit(check: Mapping[str, Any], *, company_id: int, is_super_admin: bool) -> bool:
    if check.get("company_id") is None:
        return is_super_admin
    return int(check["company_id"]) == int(company_id)


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def parse_settings(check_type: str, form: Mapping[str, Any]) -> tuple[str, dict[str, Any], int, dict[str, str]]:
    """Read the type's own fields. Returns the target, settings, duration and errors."""

    errors: dict[str, str] = {}
    settings: dict[str, Any] = {}
    target = ""
    duration = _int(form.get("duration_minutes"), 0, 0, MAX_DURATION_MINUTES)

    def threshold(field: str, *, low: float = 0, high: float | None = None, label: str = "a number") -> float:
        value = _number(form.get(field))
        if value is None or value < low or (high is not None and value > high):
            limit = f" from {_round(low)} to {_round(high)}" if high is not None else f" of at least {_round(low)}"
            errors[field] = f"Enter {label}{limit}."
            return 0.0
        return value

    if check_type == "disk":
        target = normalise_drive(form.get("disk_drive")) or "*"
        if target != "*" and not (len(target) == 2 and target[1] == ":") and not _MOUNT.match(target):
            errors["disk_drive"] = "Enter a drive letter such as C:, a mount point such as /var, or * for every drive."
        metric = str(form.get("disk_metric") or "free_percent")
        if metric not in DISK_METRICS:
            errors["disk_metric"] = "Choose percent or GB."
        operator = str(form.get("disk_operator") or "below")
        if operator not in COMPARISONS:
            errors["disk_operator"] = "Choose below or above."
        settings = {"metric": metric, "operator": operator, "threshold": threshold(
            "disk_threshold", high=100 if metric == "free_percent" else None, label="the free space")}
    elif check_type == "ping":
        target = str(form.get("ping_target") or "").strip()
        if not _HOST.match(target) or ".." in target:
            errors["ping_target"] = "Enter a host name or IP address to ping."
        settings = {
            "max_loss_percent": _int(form.get("ping_loss"), 100, 1, 100),
            "max_latency_ms": _int(form.get("ping_latency"), 0, 0, 60_000) or None,
        }
    elif check_type == "cpu":
        settings = {"threshold": threshold("cpu_threshold", low=1, high=100, label="a percentage")}
    elif check_type == "memory":
        metric = str(form.get("memory_metric") or "free_percent")
        if metric not in MEMORY_METRICS:
            errors["memory_metric"] = "Choose percent or GB."
        settings = {"metric": metric, "threshold": threshold(
            "memory_threshold", high=100 if metric == "free_percent" else None, label="the free memory")}
    elif check_type == "service":
        target = str(form.get("service_name") or "").strip()
        if not _SERVICE.match(target):
            errors["service_name"] = "Enter the service's name (not its display name), such as Spooler."
        expected = str(form.get("service_expected") or "running")
        if expected not in SERVICE_STATES:
            errors["service_expected"] = "Choose running or stopped."
        fix = str(form.get("service_fix") or "none")
        allowed = ("none", "start", "restart") if expected == "running" else ("none", "stop")
        if fix not in allowed:
            errors["service_fix"] = ("A service that should be running can be started or restarted."
                                     if expected == "running" else "A service that should be stopped can only be stopped.")
        settings = {"expected": expected, "fix": fix}
    elif check_type == "network":
        target = str(form.get("network_interface") or "").strip()
        if not _INTERFACE.match(target):
            errors["network_interface"] = "That interface name is not valid."
        direction = str(form.get("network_direction") or "total")
        if direction not in NETWORK_DIRECTIONS:
            errors["network_direction"] = "Choose received, sent or both."
        operator = str(form.get("network_operator") or "above")
        if operator not in COMPARISONS:
            errors["network_operator"] = "Choose below or above."
        settings = {"direction": direction, "operator": operator,
                    "threshold_mbps": threshold("network_threshold", label="the throughput in Mbps")}
    elif check_type == "script":
        expect = str(form.get("script_expect") or "exit_code")
        if expect not in SCRIPT_EXPECTATIONS:
            errors["script_expect"] = "Choose what the script must return."
        value = str(form.get("script_value") or "").strip()[:1024]
        if expect == "exit_code":
            value = value or "0"
            if not re.fullmatch(r"-?\d{1,10}", value):
                errors["script_value"] = "Exit codes are whole numbers."
        elif not value:
            errors["script_value"] = "Enter the text to look for."
        settings = {"expect": expect, "value": value,
                    "interval_minutes": _int(form.get("script_interval"), DEFAULT_SCRIPT_INTERVAL,
                                             MIN_SCRIPT_INTERVAL, MAX_SCRIPT_INTERVAL)}
    elif check_type == "offline":
        duration = _int(form.get("duration_minutes"), 30, MIN_OFFLINE_MINUTES, MAX_DURATION_MINUTES)
    return target, settings, duration, errors


async def _script_entries(form: Mapping[str, Any], prefix: str, script: Mapping[str, Any],
                          previous_blob: Any, same_script: bool) -> dict[str, str]:
    previous = rmm_automation.decrypt_entries(previous_blob) if same_script else {}
    entries: dict[str, str] = {}
    # A newly chosen script's fields appear after saving; only the current
    # script's values are read from the form.
    if not same_script:
        return entries
    for field_def in rmm_scripts.script_fields(script):
        key = field_def["key"]
        text = str(form.get(prefix + key) or "")[:65535]
        if not text.strip() and field_def["sensitive"] and previous.get(key):
            text = previous[key]  # a blank secret keeps the saved one
        if text.strip():
            entries[key] = text
    return entries


async def save_check(
    *,
    check_id: int | None,
    company_id: int,
    form: Mapping[str, Any],
    user_id: int | None,
    is_super_admin: bool,
) -> int:
    """Create or update a check from the checks page. ``company_id`` is the
    company the technician is working in."""

    errors: dict[str, str] = {}
    existing = await repo.get_check(check_id, with_secrets=True) if check_id is not None else None
    name = str(form.get("name") or "").strip()[:255]
    if not name:
        errors["name"] = "Give the check a name."
    scope = str(form.get("scope") or "company")
    check_company_id: int | None = company_id
    asset_id: int | None = None
    if scope == "global":
        if not is_super_admin:
            errors["scope"] = "Only super admins can add checks for every company."
        check_company_id = None
    elif scope == "asset":
        try:
            asset_id = int(str(form.get("asset_id") or ""))
        except ValueError:
            asset_id = None
        asset = await assets_repo.get_asset_by_id(asset_id) if asset_id else None
        if not asset or int(asset.get("company_id") or 0) != int(company_id):
            errors["asset_id"] = "Choose a device in this company."
    elif scope != "company":
        errors["scope"] = "Choose who the check applies to."
    raw_rules = form.get("target_rules")
    target_mode = str(form.get("target_mode") or ("rules" if raw_rules is not None else "all")).strip().lower()
    target_rules = None
    if target_mode == "rules":
        try:
            if isinstance(raw_rules, str):
                raw_rules = json.loads(raw_rules)
            target_rules = rmm_targeting.validate_rules(raw_rules)
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            errors["target_rules"] = str(exc) or "Add at least one valid targeting condition."
    elif target_mode != "all":
        errors["target_mode"] = "Choose all devices in scope or advanced rules."
    check_type = str(form.get("check_type") or "")
    if check_type not in CHECK_TYPES:
        errors["check_type"] = "Choose what to check."
        target, settings, duration = "", {}, 0
    else:
        target, settings, duration, type_errors = parse_settings(check_type, form)
        errors.update(type_errors)
    action = str(form.get("action") or "none")
    if action not in ACTIONS:
        errors["action"] = "Choose what happens when the check fails."

    check_script: dict[str, Any] | None = None
    check_entries: dict[str, str] = {}
    if check_type == "script":
        try:
            check_script = await rmm_automation._scoped_script(form.get("check_script_id"), check_company_id)
        except RunRequestError as exc:
            errors["check_script_id"] = "; ".join(exc.errors.values())
        if check_script is not None:
            target = str(check_script["id"])
            same = bool(existing) and existing.get("check_script_id") == check_script["id"]
            check_entries = await _script_entries(
                form, "check_entry:", check_script, (existing or {}).get("check_entries_encrypted"), same)

    schedule = rmm_event_log_rules.parse_schedule(form if action == "schedule_script" else None, errors)
    script: dict[str, Any] | None = None
    entries: dict[str, str] = {}
    if action in SCRIPT_ACTIONS:
        try:
            script = await rmm_automation._scoped_script(form.get("script_id"), check_company_id)
        except RunRequestError as exc:
            errors.update(exc.errors)
        if script is not None:
            same = bool(existing) and existing.get("script_id") == script["id"]
            entries = await _script_entries(form, "entry:", script, (existing or {}).get("entries_encrypted"), same)
    if errors:
        raise CheckError(errors)

    values = {
        "name": name,
        "company_id": check_company_id,
        "asset_id": asset_id,
        "target_rules": target_rules,
        "check_type": check_type,
        "target": target,
        "settings": settings,
        "duration_minutes": duration,
        "check_script_id": int(check_script["id"]) if check_script else None,
        "check_entries_encrypted": encrypt_secret(json.dumps(check_entries)) if check_entries else None,
        "check_inputs": rmm_scripts.stored_inputs(check_script, check_entries) if check_script else [],
        "action": action,
        "script_id": int(script["id"]) if script else None,
        "entries_encrypted": encrypt_secret(json.dumps(entries)) if entries else None,
        "inputs": rmm_scripts.stored_inputs(script, entries) if script else [],
        "timeout_seconds": rmm_scripts.clamp_timeout(
            form.get("timeout_seconds"),
            int(((check_script if check_type == "script" else script) or {}).get("default_timeout_seconds") or 600),
        ),
        "cooldown_minutes": _int(form.get("cooldown_minutes"), DEFAULT_COOLDOWN_MINUTES, 0, MAX_COOLDOWN_MINUTES),
        **schedule,
        "is_enabled": str(form.get("is_enabled") or "").lower() in {"1", "on", "true", "yes"},
        "user_id": user_id,
    }
    if existing is None:
        return await repo.create_check(values)
    await repo.update_check(int(existing["id"]), values)
    return int(existing["id"])


def script_values(check: Mapping[str, Any] | None, script: Mapping[str, Any] | None, *,
                  own: bool = False) -> list[dict[str, Any]]:
    """A script's fields with the check's saved values; secrets are never sent back.

    ``own`` reads the values of a script check's own script, else the action's."""

    if not check or not script:
        return []
    entries = rmm_automation.decrypt_entries(check.get("check_entries_encrypted" if own else "entries_encrypted"))
    fields = []
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        secret = bool(field_def["sensitive"]) and not rmm_scripts._TOKEN.search(value)
        fields.append({**field_def, "value": "" if secret else value, "value_set": bool(value)})
    return fields


# --------------------------------------------------------------------------- #
# Recording results and acting on them
# --------------------------------------------------------------------------- #


def check_context(check: Mapping[str, Any], agent: Mapping[str, Any], result: str) -> dict[str, Any]:
    return {"check": {
        "name": str(check.get("name") or ""),
        "type": CHECK_TYPES.get(str(check.get("check_type")), str(check.get("check_type"))),
        "target": str(check.get("target") or ""),
        "result": result,
        "device": rmm_actions.device_label(agent),
    }}


async def _act_on_failure(check: Mapping[str, Any], agent: Mapping[str, Any], state: dict[str, Any],
                          result: str, now: datetime, *, repeat: bool = False) -> datetime | None:
    """Take the check's action. Returns when a scheduled script should run, which
    the caller stores once the device's state row exists."""

    check_id = int(check["id"])
    agent_id = int(agent["id"])
    device = rmm_actions.device_label(agent)
    summary = f"Failed on {device}: {result}"
    action = check.get("action")
    if action == "run_script":
        run_id, ran, attempted = await rmm_actions.run_script(
            script_id=check.get("script_id"),
            entries_encrypted=check.get("entries_encrypted"),
            inputs=check.get("inputs"),
            timeout_seconds=check.get("timeout_seconds"),
            agent=agent,
            run_source=RUN_SOURCE,
            extra_context=check_context(check, agent, result),
        )
        state["last_action_at"] = now
        summary += f". {ran}{'.' if run_id is not None else ''}"
        log_info("RMM check ran a script", check_id=check_id, agent_id=agent_id, run_id=run_id, attempted=attempted)
    elif action == "schedule_script" and not repeat:
        run_at = rmm_event_log_rules.scheduled_time(check, now)
        name = check.get("script_name") or "the script"
        summary += f". Scheduled {name} for {run_at.isoformat(timespec='minutes')} UTC."
        await repo.record_failure(check_id, summary)
        log_info("RMM check scheduled a script", check_id=check_id, agent_id=agent_id, run_at=run_at.isoformat())
        return run_at
    elif action == "create_ticket" and not repeat:
        reference = f"rmm-check:{check_id}:{agent_id}"
        body = (f"<p>Check <strong>{rmm_actions.escape(check['name'])}</strong> is failing on "
                f"{rmm_actions.escape(device)}.</p><p>{rmm_actions.escape(describe(check))}.</p>"
                f"<p><strong>Result:</strong> {rmm_actions.escape(result)}</p>")
        ticket = await rmm_actions.open_ticket(state.get("ticket_id"))
        if ticket:
            await rmm_actions.add_ticket_update(ticket, body=body, reference=reference)
            summary += f". Updated ticket {rmm_actions.ticket_label(ticket)}."
        else:
            created = await rmm_actions.raise_ticket(
                agent=agent, subject=f"{check['name']}: {device}", description=body, reference=reference)
            state["ticket_id"] = int(created["id"])
            summary += f". Raised ticket {rmm_actions.ticket_label(created)}."
        state["last_action_at"] = now
    await repo.record_failure(check_id, summary)
    return None


async def _on_recovery(check: Mapping[str, Any], agent: Mapping[str, Any], state: dict[str, Any], result: str) -> None:
    device = rmm_actions.device_label(agent)
    summary = f"Passing again on {device}."
    if check.get("action") == "schedule_script" and await repo.clear_scheduled(int(check["id"]), int(agent["id"])):
        summary += " Dropped the scheduled script."
    ticket = await rmm_actions.open_ticket(state.get("ticket_id")) if check.get("action") == "create_ticket" else None
    if ticket:
        await rmm_actions.add_ticket_update(
            ticket,
            body=(f"<p>Check <strong>{rmm_actions.escape(check['name'])}</strong> is passing again on "
                  f"{rmm_actions.escape(device)}.</p><p><strong>Result:</strong> {rmm_actions.escape(result)}</p>"),
            reference=f"rmm-check:{int(check['id'])}:{int(agent['id'])}",
        )
        summary += f" Noted on ticket {rmm_actions.ticket_label(ticket)}."
    await repo.set_summary(int(check["id"]), summary)


def _blank_state() -> dict[str, Any]:
    return {"status": "pending", "value_text": None, "problem_since": None, "checked_at": None, "changed_at": None,
            "last_action_at": None, "ticket_id": None, "run_id": None, "next_run_at": None, "scheduled_at": None}


async def record_result(check: Mapping[str, Any], agent: Mapping[str, Any], problem: bool, result: str, *,
                        now: datetime | None = None, state: dict[str, Any] | None = None,
                        duration_minutes: int | None = None) -> dict[str, Any]:
    """Store one result for a device and act when the check starts (or keeps) failing."""

    now = now or _utcnow()
    check_id, agent_id = int(check["id"]), int(agent["id"])
    if state is None:
        state = await repo.get_state(check_id, agent_id) or _blank_state()
    state = {**_blank_state(), **state}
    before = dict(state)
    previous = state["status"]
    state["checked_at"] = now
    state["value_text"] = result[:512]
    run_at: datetime | None = None
    duration = timedelta(minutes=int(check.get("duration_minutes") or 0) if duration_minutes is None else duration_minutes)
    try:
        if problem:
            if state["problem_since"] is None:
                state["problem_since"] = now
            if now - state["problem_since"] >= duration:
                if previous != "failing":
                    state["status"], state["changed_at"] = "failing", now
                    run_at = await _act_on_failure(check, agent, state, result, now)
                elif check.get("action") == "run_script" and int(check.get("cooldown_minutes") or 0) > 0:
                    last = state.get("last_action_at")
                    if last is None or now - last >= timedelta(minutes=int(check["cooldown_minutes"])):
                        await _act_on_failure(check, agent, state, result, now, repeat=True)
            elif previous != "warning":
                state["status"], state["changed_at"] = "warning", now
        else:
            state["problem_since"] = None
            if previous != "passing":
                state["status"], state["changed_at"] = "passing", now
                if previous == "failing":
                    await _on_recovery(check, agent, state, result)
    except Exception as exc:  # noqa: BLE001 - a failed action must not lose the result
        log_error("RMM check action failed", check_id=check_id, agent_id=agent_id, error=str(exc))
    # Results that change nothing are only written now and then, so checks
    # judged every few seconds (agent offline) do not write every time.
    unchanged = all(state[key] == before.get(key) for key in state if key != "checked_at")
    last_written = before.get("checked_at")
    if unchanged and last_written is not None and now - last_written < UNCHANGED_WRITE_INTERVAL:
        state["checked_at"] = last_written
        return state
    await repo.save_state(check_id, agent_id, state)
    if run_at is not None:
        try:
            if await repo.schedule_action(check_id, agent_id, run_at):
                state["scheduled_at"] = run_at
        except Exception as exc:  # noqa: BLE001 - the result is already stored
            log_error("RMM check could not schedule its script", check_id=check_id, agent_id=agent_id, error=str(exc))
    return state


# --------------------------------------------------------------------------- #
# What the agent measures
# --------------------------------------------------------------------------- #


def agent_config_for(checks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    kinds = {check["check_type"] for check in checks}
    pings: list[str] = []
    services: dict[str, dict[str, str]] = {}
    for check in checks:
        if check["check_type"] == "ping" and check["target"] not in pings and len(pings) < MAX_PINGS:
            pings.append(check["target"])
        elif check["check_type"] == "service" and len(services) < MAX_SERVICES:
            settings = check.get("settings") or {}
            current = services.get(check["target"].casefold())
            fix = settings.get("fix") or "none"
            # Two checks for one service: the one that does something wins.
            if current is None or (current["fix"] == "none" and fix != "none"):
                services[check["target"].casefold()] = {
                    "name": check["target"], "expected": settings.get("expected") or "running", "fix": fix,
                }
    return {
        "enabled": bool(kinds & set(AGENT_TYPES)),
        "interval_seconds": REPORT_INTERVAL_SECONDS,
        "config_seconds": CONFIG_REFRESH_SECONDS,
        "cpu": "cpu" in kinds,
        "memory": "memory" in kinds,
        "disks": "disk" in kinds,
        "network": "network" in kinds,
        "pings": pings,
        "services": list(services.values()),
    }


async def agent_config(agent: Mapping[str, Any]) -> dict[str, Any]:
    """What the device's RMM agent measures and reports."""

    return agent_config_for(await checks_for_agent(agent))


def _disk_name(value: Any) -> str:
    return normalise_drive(value).casefold()


def _format_gb(value: float) -> str:
    return f"{_round(value / GB)} GB"


def evaluate(check: Mapping[str, Any], report: Mapping[str, Any]) -> tuple[bool, str] | None:
    """Whether a report shows a problem for the check, and the result in words.

    None when the report has nothing for it."""

    kind = check["check_type"]
    settings = check.get("settings") or {}
    target = check.get("target") or ""

    def compare(value: float, operator: str, limit: float) -> bool:
        return value < limit if operator == "below" else value > limit

    if kind == "disk":
        disks = [disk for disk in report.get("disks") or [] if float(disk.get("total_bytes") or 0) > 0]
        if target not in ("", "*"):
            disks = [disk for disk in disks if _disk_name(disk.get("name")) == target.casefold()]
            if not disks:
                return (True, f"{target} was not found on the device") if report.get("disks") is not None else None
        if not disks:
            return None
        metric = settings.get("metric") or "free_percent"
        limit = float(settings.get("threshold") or 0)
        results = []
        for disk in disks:
            free = float(disk.get("free_bytes") or 0)
            total = float(disk["total_bytes"])
            value = 100 * free / total if metric == "free_percent" else free / GB
            text = f"{normalise_drive(disk.get('name'))} {_format_gb(free)} free ({_round(100 * free / total)}%)"
            results.append((compare(value, settings.get("operator") or "below", limit), value, text))
        bad = [item for item in results if item[0]]
        if bad:
            return True, "; ".join(item[2] for item in bad)
        worst = min(results, key=lambda item: item[1])
        return False, worst[2] if len(results) == 1 else f"Lowest: {worst[2]}"
    if kind == "ping":
        ping = next((item for item in report.get("pings") or []
                     if str(item.get("target") or "").casefold() == target.casefold()), None)
        if not ping or not int(ping.get("sent") or 0):
            return None
        sent, received = int(ping["sent"]), int(ping.get("received") or 0)
        loss = 100 * (sent - min(received, sent)) / sent
        average = _number(ping.get("avg_ms"))
        text = f"{received}/{sent} replies" + (f", {_round(average)} ms" if received and average is not None else "")
        if ping.get("error") and not received:
            text += f" ({str(ping['error'])[:200]})"
        latency = settings.get("max_latency_ms")
        slow = bool(latency and received and average is not None and average > float(latency))
        return loss >= float(settings.get("max_loss_percent") or 100) or slow, text
    if kind == "cpu":
        value = _number(report.get("cpu_percent"))
        if value is None:
            return None
        return value > float(settings.get("threshold") or 100), f"{_round(value)}% CPU"
    if kind == "memory":
        memory = report.get("memory") or {}
        total, available = _number(memory.get("total_bytes")), _number(memory.get("available_bytes"))
        if not total or available is None:
            return None
        percent = 100 * available / total
        value = percent if settings.get("metric") == "free_percent" else available / GB
        return value < float(settings.get("threshold") or 0), f"{_format_gb(available)} free ({_round(percent)}%)"
    if kind == "service":
        service = next((item for item in report.get("services") or []
                        if str(item.get("name") or "").casefold() == target.casefold()), None)
        if not service:
            return None
        status = str(service.get("status") or "unknown").lower()
        expected = settings.get("expected") or "running"
        text = "Not installed" if status == "missing" else f"{status.replace('_', ' ').capitalize()}"
        if service.get("action"):
            text += f"; the agent {str(service['action'])[:200]}"
        if service.get("error"):
            text += f" ({str(service['error'])[:200]})"
        # A service the agent had to fix still counts as a problem this time.
        return bool(service.get("action")) or status != expected, text
    if kind == "network":
        interfaces = report.get("network")
        if interfaces is None:
            return None
        if target:
            interfaces = [item for item in interfaces if str(item.get("name") or "").casefold() == target.casefold()]
            if not interfaces:
                return True, f"{target} was not found on the device"
        received = sum(float(item.get("rx_bps") or 0) for item in interfaces) * 8 / 1_000_000
        sent = sum(float(item.get("tx_bps") or 0) for item in interfaces) * 8 / 1_000_000
        direction = settings.get("direction") or "total"
        value = received if direction == "in" else sent if direction == "out" else received + sent
        text = f"{_round(received)} Mbps received, {_round(sent)} Mbps sent"
        return compare(value, settings.get("operator") or "above", float(settings.get("threshold_mbps") or 0)), text
    return None


def _sample_time(now: datetime) -> datetime:
    minutes = get_settings().stats_sample_minutes
    start = now.replace(second=0, microsecond=0)
    return start - timedelta(minutes=(start.hour * 60 + start.minute) % minutes)


def samples_from(agent: Mapping[str, Any], report: Mapping[str, Any], kinds: set[str],
                 now: datetime) -> list[dict[str, Any]]:
    """The values in a report worth keeping for the device's charts."""

    rows: list[tuple[str, str, float]] = []
    if "cpu" in kinds and _number(report.get("cpu_percent")) is not None:
        rows.append(("cpu_percent", "", float(report["cpu_percent"])))
    memory = report.get("memory") or {}
    if "memory" in kinds and _number(memory.get("total_bytes")) and _number(memory.get("available_bytes")) is not None:
        total, available = float(memory["total_bytes"]), float(memory["available_bytes"])
        rows += [("memory_used_percent", "", 100 * (total - available) / total), ("memory_free_bytes", "", available)]
    if "disk" in kinds:
        for disk in report.get("disks") or []:
            total = _number(disk.get("total_bytes"))
            free = _number(disk.get("free_bytes"))
            if total and free is not None:
                name = normalise_drive(disk.get("name"))[:255]
                rows += [("disk_free_percent", name, 100 * free / total), ("disk_free_bytes", name, free)]
    if "network" in kinds:
        for item in report.get("network") or []:
            name = str(item.get("name") or "")[:255]
            rows += [("net_in_bps", name, float(_number(item.get("rx_bps")) or 0) * 8),
                     ("net_out_bps", name, float(_number(item.get("tx_bps")) or 0) * 8)]
    if "ping" in kinds:
        for ping in report.get("pings") or []:
            sent = int(ping.get("sent") or 0)
            if not sent:
                continue
            target = str(ping.get("target") or "")[:255]
            received = min(int(ping.get("received") or 0), sent)
            rows.append(("ping_loss_percent", target, 100 * (sent - received) / sent))
            if received and _number(ping.get("avg_ms")) is not None:
                rows.append(("ping_ms", target, float(ping["avg_ms"])))
    sampled_at = _sample_time(now)
    return [{
        "company_id": agent.get("company_id"), "asset_id": agent.get("asset_id"), "agent_id": int(agent["id"]),
        "metric": metric, "instance": instance, "value": round(value, 3), "sampled_at": sampled_at,
    } for metric, instance, value in rows if math.isfinite(value)]


async def ingest_report(agent: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, int]:
    """Check an agent's report against its checks and keep its stats."""

    now = _utcnow()
    checks = await checks_for_agent(agent, with_secrets=True)
    states = await repo.list_states_for_agent(int(agent["id"]))
    results = {"checked": 0, "failing": 0, "stored": 0}
    for check in checks:
        if check["check_type"] not in AGENT_TYPES:
            continue
        outcome = evaluate(check, report)
        if outcome is None:
            continue
        state = await record_result(check, agent, outcome[0], outcome[1], now=now, state=states.get(int(check["id"])))
        results["checked"] += 1
        results["failing"] += 1 if state["status"] == "failing" else 0
    kinds = {check["check_type"] for check in checks} & set(STATS_TYPES)
    if kinds and await stats_db.ensure_connected():
        try:
            results["stored"] = await stats_repo.insert_samples(samples_from(agent, report, kinds, now))
        except Exception as exc:  # noqa: BLE001 - checks still count without history
            log_error("Could not store RMM device stats", agent_id=agent.get("id"), error=str(exc))
    return results


# --------------------------------------------------------------------------- #
# Checks run by the server: agent offline and scripts
# --------------------------------------------------------------------------- #


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours"
    return f"{hours // 24} days"


async def evaluate_offline(now: datetime | None = None) -> int:
    """Check every agent's last check-in against its offline checks. Returns how many results were recorded."""

    now = now or _utcnow()
    checks = await repo.list_checks_of_types(["offline"], with_secrets=True)
    if not checks:
        return 0
    recorded = 0
    agents = await repo.list_active_agents()
    selected = await targeted_checks_for_agents(checks, agents)
    for agent in agents:
        applicable = selected[int(agent["id"])]
        if not applicable:
            continue
        states = await repo.list_states_for_agent(int(agent["id"]))
        last_seen = agent.get("last_seen_utc")
        for check in applicable:
            if last_seen is None:
                problem, result = True, "Has never checked in"
            else:
                away = now - last_seen
                problem = away >= timedelta(minutes=int(check.get("duration_minutes") or 30))
                result = f"Last checked in {_ago(away)} ago" if away >= timedelta(minutes=1) else "Checked in just now"
            await record_result(check, agent, problem, result, now=now, state=states.get(int(check["id"])),
                                duration_minutes=0)
            recorded += 1
    return recorded


_RUN_WAITING = ("queued", "dispatched", "running")


def script_outcome(check: Mapping[str, Any], run: Mapping[str, Any]) -> tuple[bool, str]:
    settings = check.get("settings") or {}
    expect = settings.get("expect") or "exit_code"
    wanted = str(settings.get("value") if settings.get("value") not in (None, "") else "0")
    output = str(run.get("stdout") or "").strip()
    first_line = output.splitlines()[0][:200] if output else ""
    status = str(run.get("status") or "")
    if status in ("timed_out", "expired", "cancelled") or (run.get("exit_code") is None and status != "completed"):
        reason = {"timed_out": "timed out", "expired": "did not reach the device in time",
                  "cancelled": "was cancelled"}.get(status, "did not finish")
        return True, f"The script {reason}" + (f": {run['error_message']}"[:300] if run.get("error_message") else "")
    code = run.get("exit_code")
    text = f"Exit code {code}" + (f": {first_line}" if first_line else "")
    if expect == "exit_code":
        try:
            return int(code) != int(wanted), text
        except (TypeError, ValueError):
            return True, text
    if expect == "output_equals":
        return output != wanted, text
    found = wanted.casefold() in output.casefold()
    return (not found if expect == "output_contains" else found), text


async def run_due_script_checks(now: datetime | None = None) -> int:
    """Queue script checks that are due on each device. Returns how many runs were queued."""

    now = now or _utcnow()
    checks = await repo.list_checks_of_types(["script"], with_secrets=True)
    if not checks:
        return 0
    queued = 0
    agents = await repo.list_active_agents()
    selected = await targeted_checks_for_agents(checks, agents)
    for agent in agents:
        applicable = selected[int(agent["id"])]
        if not applicable:
            continue
        states = await repo.list_states_for_agent(int(agent["id"]))
        full_agent = None
        for check in applicable:
            state = {**_blank_state(), **(states.get(int(check["id"])) or {})}
            if state["next_run_at"] is not None and state["next_run_at"] > now:
                continue
            try:
                if state["run_id"]:
                    run = await rmm_repo.get_run(int(state["run_id"]))
                    if run and run.get("status") in _RUN_WAITING:
                        continue  # still waiting for the last run
                    if run:  # finished without reporting here (expired or cancelled)
                        state["run_id"] = None
                        state = await record_result(check, agent, *script_outcome(check, run), now=now, state=state)
                full_agent = full_agent or await rmm_repo.get_agent(int(agent["id"]))
                interval = timedelta(minutes=int((check.get("settings") or {}).get("interval_minutes")
                                                 or DEFAULT_SCRIPT_INTERVAL))
                state["next_run_at"] = now + interval
                run_id, summary, attempted = await rmm_actions.run_script(
                    script_id=check.get("check_script_id"),
                    entries_encrypted=check.get("check_entries_encrypted"),
                    inputs=check.get("check_inputs"),
                    timeout_seconds=check.get("timeout_seconds"),
                    agent=full_agent or agent,
                    run_source=RUN_SOURCE,
                    extra_context=check_context(check, agent, ""),
                )
                state["run_id"] = run_id
                if run_id is None:
                    await repo.set_summary(int(check["id"]), summary)
                else:
                    queued += 1
                await repo.save_state(int(check["id"]), int(agent["id"]), state)
            except Exception as exc:  # noqa: BLE001 - one device must not stop the others
                log_error("RMM script check failed", check_id=check["id"], agent_id=agent["id"], error=str(exc))
    return queued


async def run_finished(run_id: int) -> int:
    """Judge a script check's run once the agent reports it. Returns how many checks it answered."""

    states = await repo.list_states_for_run(run_id)
    if not states:
        return 0
    run = await rmm_repo.get_run(run_id)
    if not run:
        return 0
    agents = {}
    for agent_id in {int(state["agent_id"]) for state in states}:
        agent = await rmm_repo.get_agent(agent_id)
        if agent:
            agents[agent_id] = agent
    candidates = await repo.list_checks_of_types(["script"], with_secrets=True)
    existing_check_ids = {int(check["id"]) for check in candidates}
    selected = await targeted_checks_for_agents(candidates, agents.values())
    applicable = {agent_id: {int(check["id"]): check for check in checks}
                  for agent_id, checks in selected.items()}
    answered = 0
    for state in states:
        check_id, agent_id = int(state["check_id"]), int(state["agent_id"])
        check = applicable.get(agent_id, {}).get(check_id)
        agent = agents.get(agent_id)
        if not agent or check_id not in existing_check_ids:
            continue  # removed or changed since the run was queued
        state["run_id"] = None
        if not check:
            # A run queued under earlier inventory cannot trigger actions
            # after the device leaves the check's target or is overridden.
            await repo.save_state(check_id, agent_id, state)
            continue
        await record_result(check, agent, *script_outcome(check, run), state=state)
        answered += 1
    return answered


async def run_due_scheduled_checks(now: datetime | None = None) -> int:
    """Run the scripts check actions scheduled for now or earlier. Returns how many were queued."""

    now = now or _utcnow()
    pending_checks = await repo.due_scheduled(now)
    if not pending_checks:
        return 0
    agents = {}
    for agent_id in {int(pending["agent_id"]) for pending in pending_checks}:
        try:
            agent = await rmm_repo.get_agent(agent_id)
            if agent:
                agents[agent_id] = agent
        except Exception as exc:  # noqa: BLE001 - one device must not stop the others
            log_error("RMM check scheduled device lookup failed", agent_id=agent_id, error=str(exc))
    candidates = await repo.list_checks_of_types(CHECK_TYPES, with_secrets=True)
    selected = await targeted_checks_for_agents(candidates, agents.values())
    applicable = {agent_id: {int(check["id"]): check for check in checks}
                  for agent_id, checks in selected.items()}
    queued = 0
    for pending in pending_checks:
        check_id, agent_id = int(pending["check_id"]), int(pending["agent_id"])
        if not await repo.claim_scheduled(check_id, agent_id, now):
            continue  # another instance took it
        try:
            check = applicable.get(agent_id, {}).get(check_id)
            agent = agents.get(agent_id)
            if (not check or not check.get("is_enabled") or check.get("action") != "schedule_script" or not agent
                    or pending.get("status") != "failing"):
                continue  # no longer applicable, changed, passing again or removed
            run_id, summary, attempted = await rmm_actions.run_script(
                script_id=check.get("script_id"),
                entries_encrypted=check.get("entries_encrypted"),
                inputs=check.get("inputs"),
                timeout_seconds=check.get("timeout_seconds"),
                agent=agent,
                run_source=RUN_SOURCE,
                extra_context=check_context(check, agent, str(pending.get("value_text") or "")),
            )
            if run_id is not None:
                queued += 1
                summary += " as scheduled."
            await repo.set_summary(check_id, summary)
            log_info("RMM check ran its scheduled script", check_id=check_id, agent_id=agent_id, run_id=run_id,
                     attempted=attempted)
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the others
            log_error("RMM check scheduled run failed", check_id=check_id, agent_id=agent_id, error=str(exc))
    return queued


async def tick() -> None:
    """Server-side checks; called by the RMM automation loop on one instance at a time."""

    await evaluate_offline()
    await run_due_script_checks()
    await run_due_scheduled_checks()


async def prune_stats() -> int:
    """Delete device stats older than ``STATS_RETENTION_DAYS`` (0 keeps them all)."""

    days = get_settings().stats_retention_days
    if days <= 0 or not await stats_db.ensure_connected():
        return 0
    removed = await stats_repo.prune_samples(_utcnow() - timedelta(days=days))
    if removed:
        logger.info("Pruned {count} RMM device stats samples older than {days} days", count=removed, days=days)
    return removed
