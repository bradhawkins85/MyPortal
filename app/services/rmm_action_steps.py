"""The ordered actions RMM checks and event log rules take.

When a check (:mod:`app.services.rmm_checks`) fails, or an event log rule
(:mod:`app.services.rmm_event_log_rules`) matches an event, it takes its
actions one after another in the order the technician arranged them: create a
ticket, run a script, schedule a script for later or (checks) install a
Chocolatey package. Any combination is allowed, including several scripts, but
only one ticket, since one ticket per device tracks the problem.

Each action is a dict with a ``type`` (:data:`TYPES`) and a short ``key`` that
stays with it when the list is reordered; a scheduled script waits per action
and device under that key. Its own settings follow:

* scripts: ``script_id``, ``entries_encrypted`` (values, encrypted as for
  schedules), ``inputs`` (the masked copy for run history) and
  ``timeout_seconds``; a scheduled script also ``schedule_mode`` and
  ``delay_minutes`` or ``run_time`` and ``schedule_timezone``;
* Chocolatey: ``package_id``;
* tickets: ``auto_close_when_resolved`` (checks).

They are stored as JSON (``actions_json``). A check or rule saved before then
has its one action in older columns, read by :func:`steps`; those columns are
still written from the first action (:func:`legacy_columns`) so an earlier
release keeps working with what it understands.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.repositories import rmm as rmm_repo
from app.security.encryption import encrypt_secret
from app.services import rmm_automation, rmm_scripts
from app.services.rmm_scripts import RunRequestError

TYPES = {
    "create_ticket": "Create a ticket",
    "run_script": "Run a script",
    "schedule_script": "Schedule a script",
    "install_choco": "Install a Chocolatey package",
}
SCRIPT_TYPES = ("run_script", "schedule_script")
# Actions that happen again while a check keeps failing, once its cooldown passes.
REPEATABLE_TYPES = ("run_script", "install_choco")
MAX_STEPS = 10
LEGACY_KEY = "1"
_KEY = re.compile(r"^[a-z0-9]{1,16}$")

SCHEDULE_MODES = {"delay": "After a delay", "time": "At the next time of day"}
DEFAULT_DELAY_MINUTES = 60
MAX_DELAY_MINUTES = 7 * 24 * 60
_RUN_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TRUE = {"1", "on", "true", "yes"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def new_key(taken: Iterable[str] = ()) -> str:
    """A key for a new action that no other action of the list uses."""

    used = set(taken)
    while True:
        key = secrets.token_hex(4)
        if key not in used:
            return key


def form_list(form: Mapping[str, Any], name: str) -> list[str]:
    """Every value posted for ``name``, from a real form or a plain dict."""

    if hasattr(form, "getlist"):
        return [str(value) for value in form.getlist(name)]
    value = form.get(name)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


# --------------------------------------------------------------------------- #
# Reading stored actions
# --------------------------------------------------------------------------- #


def legacy_steps(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The one action of a check or rule saved before actions were listed."""

    kind = row.get("action")
    if kind not in TYPES:
        return []
    step: dict[str, Any] = {"key": LEGACY_KEY, "type": kind}
    if kind in SCRIPT_TYPES:
        step.update(
            script_id=row.get("script_id"), entries_encrypted=row.get("entries_encrypted"),
            inputs=row.get("inputs") or [], timeout_seconds=row.get("timeout_seconds") or 600,
            script_name=row.get("script_name"),
        )
        if kind == "schedule_script":
            step.update(schedule_mode=row.get("schedule_mode") or "delay", delay_minutes=row.get("delay_minutes"),
                        run_time=row.get("run_time"), schedule_timezone=row.get("schedule_timezone"))
    elif kind == "install_choco":
        step["package_id"] = str((row.get("settings") or {}).get("package_id") or "")
    elif kind == "create_ticket":
        step["auto_close_when_resolved"] = bool(row.get("auto_close_when_resolved"))
    return [step]


def steps(row: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """A check's or rule's actions in order, from ``actions`` or the older columns."""

    if not row:
        return []
    stored = row.get("actions")
    if isinstance(stored, list):
        return [dict(step) for step in stored
                if isinstance(step, dict) and step.get("type") in TYPES and _KEY.match(str(step.get("key") or ""))]
    return legacy_steps(row)


def find(row: Mapping[str, Any] | None, key: str | None) -> dict[str, Any] | None:
    return next((step for step in steps(row) if step["key"] == key), None)


def first_scheduled(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The action a run scheduled by an earlier release belongs to."""

    return next((step for step in steps(row) if step["type"] == "schedule_script"), None)


def has_type(row: Mapping[str, Any] | None, *kinds: str) -> bool:
    return any(step["type"] in kinds for step in steps(row))


def legacy_columns(actions: list[dict[str, Any]], *, none: str) -> dict[str, Any]:
    """The older single-action columns, written from the first action of each kind."""

    script = next((step for step in actions if step["type"] in SCRIPT_TYPES), None) or {}
    scheduled = next((step for step in actions if step["type"] == "schedule_script"), None) or {}
    ticket = next((step for step in actions if step["type"] == "create_ticket"), None) or {}
    return {
        "action": actions[0]["type"] if actions else none,
        "script_id": script.get("script_id"),
        "entries_encrypted": script.get("entries_encrypted"),
        "inputs": script.get("inputs") or [],
        "script_timeout_seconds": script.get("timeout_seconds"),
        "schedule_mode": scheduled.get("schedule_mode"),
        "delay_minutes": scheduled.get("delay_minutes"),
        "run_time": scheduled.get("run_time"),
        "schedule_timezone": scheduled.get("schedule_timezone"),
        "auto_close_when_resolved": bool(ticket.get("auto_close_when_resolved")),
        "package_id": next((step.get("package_id") for step in actions if step["type"] == "install_choco"), None),
    }


async def with_script_names(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give each row an ``actions`` list whose scripts carry their current names."""

    items = [dict(row) for row in rows]
    for item in items:
        item["actions"] = steps(item)
    wanted = {step.get("script_id") for item in items for step in item["actions"]
              if step["type"] in SCRIPT_TYPES and not step.get("script_name")}
    names = await rmm_repo.script_names(wanted) if wanted else {}
    for item in items:
        for step in item["actions"]:
            if step["type"] in SCRIPT_TYPES and not step.get("script_name") and step.get("script_id"):
                step["script_name"] = names.get(int(step["script_id"]))
    return items


# --------------------------------------------------------------------------- #
# Describing actions
# --------------------------------------------------------------------------- #


def describe_schedule(step: Mapping[str, Any]) -> str:
    if step.get("schedule_mode") == "time":
        return f"at {step.get('run_time')} ({step.get('schedule_timezone') or 'UTC'})"
    minutes = int(step.get("delay_minutes") or DEFAULT_DELAY_MINUTES)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''} later"
    return f"{minutes} minute{'s' if minutes != 1 else ''} later"


def describe(step: Mapping[str, Any]) -> str:
    """One action in words, such as "Run Cleanup 2 hours later"."""

    kind = step.get("type")
    name = step.get("script_name") or "a removed script"
    if kind == "run_script":
        return f"Run {name}"
    if kind == "schedule_script":
        return f"Run {name} {describe_schedule(step)}"
    if kind == "install_choco":
        return f"Install Chocolatey package {step.get('package_id') or ''}".rstrip()
    if kind == "create_ticket":
        return "Create a ticket" + (" and close it when resolved" if step.get("auto_close_when_resolved") else "")
    return TYPES.get(str(kind), str(kind))


def describe_all(actions: list[dict[str, Any]]) -> str:
    """Every action in order: "Create a ticket, then run Cleanup"."""

    texts = [describe(step) for step in actions]
    return ", then ".join([texts[0], *(text[:1].lower() + text[1:] for text in texts[1:])]) if texts else ""


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #


def parse_schedule(form: Mapping[str, Any] | None, errors: dict[str, str], *, prefix: str = "") -> dict[str, Any]:
    """When a "Schedule a script" action runs. ``prefix`` names the action's fields.

    No ``form`` gives the empty schedule of other actions; problems go in ``errors``."""

    schedule: dict[str, Any] = {"schedule_mode": None, "delay_minutes": None, "run_time": None,
                                "schedule_timezone": None}
    if form is None:
        return schedule
    mode = str(form.get(prefix + "schedule_mode") or "delay")
    if mode not in SCHEDULE_MODES:
        errors["schedule_mode"] = "Choose when the script runs."
    schedule["schedule_mode"] = mode
    if mode == "delay":
        schedule["delay_minutes"] = _int(form.get(prefix + "delay_minutes"), DEFAULT_DELAY_MINUTES, 1, MAX_DELAY_MINUTES)
    else:
        run_time = str(form.get(prefix + "run_time") or "").strip()[:5]
        if not _RUN_TIME.match(run_time):
            errors["run_time"] = "Enter the time as HH:MM, such as 02:00."
        schedule["run_time"] = run_time
        try:
            schedule["schedule_timezone"] = rmm_automation.validate_timezone(form.get(prefix + "schedule_timezone"))
        except RunRequestError as exc:
            errors.update(exc.errors)
    return schedule


def scheduled_time(step: Mapping[str, Any], now: datetime | None = None) -> datetime:
    """When a scheduled script started at ``now`` runs (naive UTC)."""

    now = now or _utcnow()
    if step.get("schedule_mode") != "time":
        return now + timedelta(minutes=int(step.get("delay_minutes") or DEFAULT_DELAY_MINUTES))
    zone = ZoneInfo(str(step.get("schedule_timezone") or "UTC"))
    hour, minute = (int(part) for part in str(step.get("run_time") or "00:00").split(":"))
    local_now = now.replace(tzinfo=timezone.utc).astimezone(zone)
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        target = (local_now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Re-attach the zone so a daylight saving change on the way is respected.
    target = datetime(target.year, target.month, target.day, hour, minute, tzinfo=zone)
    return target.astimezone(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def script_entries(form: Mapping[str, Any], prefix: str, script: Mapping[str, Any],
                   previous_blob: Any, same_script: bool) -> dict[str, str]:
    """A script's values from the form. A newly chosen script's fields appear
    after saving, so only the saved script's values are read; a blank secret
    keeps the saved one."""

    if not same_script:
        return {}
    previous = rmm_automation.decrypt_entries(previous_blob)
    entries: dict[str, str] = {}
    for field_def in rmm_scripts.script_fields(script):
        key = field_def["key"]
        text = str(form.get(prefix + key) or "")[:65535]
        if not text.strip() and field_def["sensitive"] and previous.get(key):
            text = previous[key]
        if text.strip():
            entries[key] = text
    return entries


def script_values(step: Mapping[str, Any] | None, script: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """A script action's fields with its saved values; secrets are never sent back."""

    if not step or not script:
        return []
    entries = rmm_automation.decrypt_entries(step.get("entries_encrypted"))
    fields = []
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        secret = bool(field_def["sensitive"]) and not rmm_scripts._TOKEN.search(value)
        fields.append({**field_def, "value": "" if secret else value, "value_set": bool(value)})
    return fields


def field_prefix(key: str) -> str:
    """The form field names of one listed action start with this."""

    return f"action:{key}:"


def error_prefix(key: str) -> str:
    """Errors for one listed action are keyed with this, and its controls' IDs end with it."""

    return f"action-{key}-"


async def _parse_step(form: Mapping[str, Any], *, key: str, kind: str, prefix: str, errors_prefix: str,
                      entry_prefix: str, company_id: int | None, previous: Mapping[str, Any] | None,
                      errors: dict[str, str]) -> dict[str, Any]:
    step: dict[str, Any] = {"key": key, "type": kind}
    problems: dict[str, str] = {}
    if kind in SCRIPT_TYPES:
        script: dict[str, Any] | None = None
        try:
            script = await rmm_automation._scoped_script(form.get(prefix + "script_id"), company_id)
        except RunRequestError as exc:
            problems.update(exc.errors)
        entries: dict[str, str] = {}
        if script is not None:
            same = previous is not None and previous.get("script_id") == script["id"]
            entries = script_entries(form, entry_prefix, script, (previous or {}).get("entries_encrypted"), same)
        step.update(
            script_id=int(script["id"]) if script else None,
            entries_encrypted=encrypt_secret(json.dumps(entries)) if entries else None,
            inputs=rmm_scripts.stored_inputs(script, entries) if script else [],
            timeout_seconds=rmm_scripts.clamp_timeout(
                form.get(prefix + "timeout_seconds"), int((script or {}).get("default_timeout_seconds") or 600)),
        )
        if kind == "schedule_script":
            step.update(parse_schedule(form, problems, prefix=prefix))
    elif kind == "install_choco":
        from app.services.rmm_software_updates import package_ids

        try:
            step["package_id"] = package_ids([form.get(prefix + "package_id")])[0]
        except RunRequestError as exc:
            problems.update(exc.errors)
            step["package_id"] = str(form.get(prefix + "package_id") or "").strip()[:128]
    elif kind == "create_ticket":
        step["auto_close_when_resolved"] = str(form.get(prefix + "auto_close_when_resolved") or "").lower() in _TRUE
    for name, message in problems.items():
        errors[errors_prefix + name] = message
    return step


async def parse_steps(
    form: Mapping[str, Any],
    *,
    types: Iterable[str],
    company_id: int | None,
    existing: list[dict[str, Any]],
    errors: dict[str, str],
    legacy_action: str | None = None,
) -> list[dict[str, Any]]:
    """The ordered actions posted by the action list.

    Each listed action posts its key as ``action_key`` (in order) and its own
    fields as ``action:{key}:{field}``; script values as
    ``action:{key}:entry:{field}``. A form without ``action_key`` is the single
    action of earlier releases (``legacy_action`` and unprefixed fields).
    ``company_id`` limits scripts to that company's and Common ones (None: only
    Common). Problems go in ``errors``, keyed ``action-{key}-{field}``."""

    allowed = tuple(types)
    previous = {step["key"]: step for step in existing}
    keys = form_list(form, "action_key")
    if not keys:
        if legacy_action not in allowed:
            return []
        return [await _parse_step(form, key=LEGACY_KEY, kind=str(legacy_action), prefix="", errors_prefix="",
                                  entry_prefix="entry:", company_id=company_id,
                                  previous=previous.get(LEGACY_KEY), errors=errors)]
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in keys:
        key = raw.strip().lower()
        if not _KEY.match(key) or key in seen:
            continue
        seen.add(key)
        prefix = field_prefix(key)
        kind = str(form.get(prefix + "type") or "")
        if not kind or str(form.get(prefix + "remove") or "").lower() in _TRUE:
            continue  # the blank "add an action" row, or one removed, on the form without JavaScript
        if kind not in allowed:
            errors[error_prefix(key) + "type"] = "Choose what this action does."
            continue
        actions.append(await _parse_step(
            form, key=key, kind=kind, prefix=prefix, errors_prefix=error_prefix(key), entry_prefix=prefix + "entry:",
            company_id=company_id, previous=previous.get(key), errors=errors,
        ))
    if sum(1 for step in actions if step["type"] == "create_ticket") > 1:
        errors["actions"] = "Create a ticket only once. One ticket per device follows the problem."
    if len(actions) > MAX_STEPS:
        errors["actions"] = f"Use at most {MAX_STEPS} actions."
    return actions


def posted_steps(form: Mapping[str, Any], types: Iterable[str], *, legacy_action: str | None = None) -> list[dict[str, Any]]:
    """The actions as posted, unvalidated, to show the form again after an error."""

    allowed = tuple(types)
    keys = form_list(form, "action_key")
    listed: list[tuple[str, str, str]] = []
    if keys:
        seen: set[str] = set()
        for raw in keys:
            key = raw.strip().lower()
            removed = str(form.get(field_prefix(key) + "remove") or "").lower() in _TRUE
            if _KEY.match(key) and key not in seen and not removed:
                seen.add(key)
                listed.append((key, field_prefix(key), str(form.get(field_prefix(key) + "type") or "")))
    elif legacy_action in allowed:
        listed.append((LEGACY_KEY, "", str(legacy_action)))
    actions = []
    for key, prefix, kind in listed[:MAX_STEPS * 2]:
        if kind not in allowed:
            continue
        try:
            script_id: int | None = int(str(form.get(prefix + "script_id") or ""))
        except ValueError:
            script_id = None
        actions.append({
            "key": key, "type": kind, "script_id": script_id,
            "timeout_seconds": str(form.get(prefix + "timeout_seconds") or "")[:8] or 600,
            "schedule_mode": str(form.get(prefix + "schedule_mode") or "delay")[:8],
            "delay_minutes": str(form.get(prefix + "delay_minutes") or "")[:8] or DEFAULT_DELAY_MINUTES,
            "run_time": str(form.get(prefix + "run_time") or "")[:5],
            "schedule_timezone": str(form.get(prefix + "schedule_timezone") or "")[:64],
            "package_id": str(form.get(prefix + "package_id") or "")[:128],
            "auto_close_when_resolved": str(form.get(prefix + "auto_close_when_resolved") or "").lower() in _TRUE,
        })
    return actions


def blank_steps(types: Iterable[str], key: str = "__KEY__") -> dict[str, dict[str, Any]]:
    """A new action of each type as the action list's fields; ``key`` is filled in when it is added."""

    return {kind: form_steps([{"key": key, "type": kind}])[0] for kind in types}


def form_steps(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Saved actions as the action list's fields, with defaults for what they do not use."""

    rows = []
    for step in actions:
        rows.append({
            "key": step["key"], "type": step["type"], "script_id": step.get("script_id"),
            "script_name": step.get("script_name"),
            "timeout_seconds": step.get("timeout_seconds") or 600,
            "schedule_mode": step.get("schedule_mode") or "delay",
            "delay_minutes": step.get("delay_minutes") or DEFAULT_DELAY_MINUTES,
            "run_time": step.get("run_time") or "02:00",
            "schedule_timezone": step.get("schedule_timezone") or rmm_automation.default_timezone(),
            "package_id": step.get("package_id") or "",
            "auto_close_when_resolved": bool(step.get("auto_close_when_resolved")),
        })
    return rows


async def script_details(actions: list[dict[str, Any]], stored: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """For the action list: each script action's script and, once it is saved with
    that script (``stored`` is the saved check or rule, with secrets), its values."""

    saved_steps = {step["key"]: step for step in steps(stored)}
    found: dict[str, dict[str, Any]] = {}
    for step in actions:
        if step["type"] not in SCRIPT_TYPES or not step.get("script_id"):
            continue
        script = await rmm_repo.get_script(int(step["script_id"]))
        saved = saved_steps.get(step["key"])
        same = bool(script) and bool(saved) and saved.get("script_id") == step["script_id"]
        found[step["key"]] = {"script": script, "saved": same, "fields": script_values(saved, script) if same else []}
    return found


async def script_changed(saved: list[dict[str, Any]], previous: list[dict[str, Any]]) -> bool:
    """Whether a save chose a script with values to fill in, which only show once saved."""

    before = {step["key"]: step.get("script_id") for step in previous}
    for step in saved:
        script_id = step.get("script_id")
        if step["type"] not in SCRIPT_TYPES or not script_id or before.get(step["key"]) == script_id:
            continue
        script = await rmm_repo.get_script(int(script_id))
        if script and rmm_scripts.script_fields(script):
            return True
    return False
