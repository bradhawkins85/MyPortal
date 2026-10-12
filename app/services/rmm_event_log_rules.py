"""Event log rules: drop incoming event log entries, or run an RMM script when one arrives.

A rule applies to every company (super admins only), one company, or one
device. Its conditions compare an entry's fields (:data:`FIELDS`); every
condition must match. When an agent sends a batch (:func:`apply`):

* entries matching an enabled **drop** rule are not stored, and do not start
  scripts;
* for each enabled **run script** rule that some stored entry matches, the
  rule's Gitea script is queued on the device that sent it, as an ordinary
  RMM run (``run_source = 'event_log'``). Its values are saved encrypted like a
  schedule's and may use ``{{event.*}}`` variables (:data:`EVENT_VARIABLES`)
  from the first matching entry. A rule runs at most once per device per
  ``cooldown_minutes``, so a burst of events queues one run;
* for each enabled **schedule a script** rule that some stored entry matches,
  the rule's script is queued later on that device, after ``delay_minutes``
  or at the next ``run_time`` in ``schedule_timezone``
  (:func:`_schedule_rule`). One run waits per rule and device; the RMM
  automation loop queues it when due (:func:`run_due_scheduled`);
* for each enabled **create ticket** rule that some stored entry matches, a
  ticket is raised for the device's company with the device's asset linked.
  While that ticket is not closed, later matches on the same device add an
  internal update to it instead (:func:`_ticket_rule`).

A drop rule can also be run now (:func:`drop_stored`), deleting the entries
already stored that it matches.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.core.logging import log_error, log_info
from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.repositories import rmm_event_logs as event_log_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import rmm_actions, rmm_automation, rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "event_log"
ACTIONS = {
    "drop": "Drop the event",
    "run_script": "Run a script",
    "schedule_script": "Schedule a script",
    "create_ticket": "Create a ticket",
}
SCRIPT_ACTIONS = ("run_script", "schedule_script")
SCHEDULE_MODES = {"delay": "After a delay", "time": "At the next time of day"}
DEFAULT_DELAY_MINUTES = 60
MAX_DELAY_MINUTES = 7 * 24 * 60
_RUN_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TICKET_EVENTS_LISTED = 10
FIELDS = {
    "log_name": "Log",
    "event_id": "Event ID",
    "level": "Level",
    "provider": "Source",
    "computer": "Computer",
    "message": "Message",
}
OPERATORS = {
    "is": "is",
    "is_not": "is not",
    "one_of": "is one of",
    "not_one_of": "is not one of",
    "contains": "contains",
    "not_contains": "does not contain",
    "starts_with": "starts with",
    "at_least": "is at least as severe as",
}
_TEXT_OPERATORS = ("is", "is_not", "one_of", "not_one_of", "contains", "not_contains", "starts_with")
FIELD_OPERATORS = {
    "log_name": _TEXT_OPERATORS,
    "provider": _TEXT_OPERATORS,
    "computer": _TEXT_OPERATORS,
    "message": _TEXT_OPERATORS,
    "event_id": ("is", "is_not", "one_of", "not_one_of"),
    "level": ("is", "is_not", "at_least"),
}
LEVEL_NAMES = {"critical": 1, "error": 2, "warning": 3, "information": 4, "info": 4, "verbose": 5}
MAX_CONDITIONS = 10
MAX_VALUE_LENGTH = 1024
DEFAULT_COOLDOWN_MINUTES = 60
MAX_COOLDOWN_MINUTES = 7 * 24 * 60
EVENT_VARIABLES = (
    ("Log", "{{event.log_name}}"),
    ("Event ID", "{{event.event_id}}"),
    ("Level", "{{event.level}}"),
    ("Source", "{{event.provider}}"),
    ("Computer", "{{event.computer}}"),
    ("Message", "{{event.message}}"),
    ("Record ID", "{{event.record_id}}"),
    ("Time (UTC)", "{{event.time_created}}"),
)
_LIST_SPLIT = re.compile(r"[,\n]")


class RuleError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _level_number(value: Any) -> int | None:
    text = str(value or "").strip().casefold()
    if text in LEVEL_NAMES:
        return LEVEL_NAMES[text]
    try:
        number = int(text)
    except ValueError:
        return None
    return number if 0 <= number <= 255 else None


def _entry_level(entry: Mapping[str, Any]) -> int:
    level = _level_number(entry.get("level"))
    # 0 is Windows' "log always", shown as information.
    return 4 if level in (None, 0) else level


def _split(value: str) -> list[str]:
    return [item.strip() for item in _LIST_SPLIT.split(value) if item.strip()]


def condition_matches(condition: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    field = condition.get("field")
    operator = condition.get("operator")
    value = str(condition.get("value") or "")
    if field == "level":
        wanted = _level_number(value)
        actual = _entry_level(entry)
        if wanted is None:
            return False
        if operator == "at_least":
            return 1 <= actual <= wanted
        return (actual == wanted) == (operator == "is")
    if field == "event_id":
        actual_id = str(entry.get("event_id") if entry.get("event_id") is not None else "")
        wanted_ids = _split(value) if operator in ("one_of", "not_one_of") else [value.strip()]
        found = actual_id in wanted_ids
        return found if operator in ("is", "one_of") else not found
    actual = str(entry.get(field) or "").casefold()
    target = value.casefold()
    if operator == "is":
        return actual == target.strip()
    if operator == "is_not":
        return actual != target.strip()
    if operator in ("one_of", "not_one_of"):
        found = actual in {item.casefold() for item in _split(value)}
        return found if operator == "one_of" else not found
    if operator == "contains":
        return target in actual
    if operator == "not_contains":
        return target not in actual
    if operator == "starts_with":
        return actual.startswith(target)
    return False


def rule_matches(rule: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    conditions = rule.get("conditions") or []
    return bool(conditions) and all(condition_matches(condition, entry) for condition in conditions)


def describe_condition(condition: Mapping[str, Any]) -> str:
    return " ".join([
        FIELDS.get(str(condition.get("field")), str(condition.get("field"))),
        OPERATORS.get(str(condition.get("operator")), str(condition.get("operator"))),
        f"“{condition.get('value')}”",
    ])


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def parse_conditions(fields: Iterable[Any], operators: Iterable[Any], values: Iterable[Any]) -> list[dict[str, str]]:
    """Read the condition rows of the rule form; blank rows are ignored."""

    conditions: list[dict[str, str]] = []
    errors: dict[str, str] = {}
    for index, (field, operator, value) in enumerate(zip(fields, operators, values), start=1):
        field, operator, value = str(field or ""), str(operator or ""), str(value or "").strip()
        if not value:
            continue
        if field not in FIELDS:
            errors[f"condition_{index}"] = "Choose a field."
            continue
        if operator not in FIELD_OPERATORS[field]:
            errors[f"condition_{index}"] = f"“{OPERATORS.get(operator, operator)}” does not work with {FIELDS[field]}."
            continue
        if len(value) > MAX_VALUE_LENGTH:
            errors[f"condition_{index}"] = "That value is too long."
            continue
        if field == "level" and _level_number(value) is None:
            errors[f"condition_{index}"] = "Use Critical, Error, Warning, Information or Verbose."
            continue
        if field == "event_id" and not all(item.isdigit() for item in (_split(value) or [value])):
            errors[f"condition_{index}"] = "Event IDs are numbers; separate several with commas."
            continue
        conditions.append({"field": field, "operator": operator, "value": value})
    if errors:
        raise RuleError(errors)
    if not conditions:
        raise RuleError({"conditions": "Add at least one condition, so the rule cannot match every event."})
    if len(conditions) > MAX_CONDITIONS:
        raise RuleError({"conditions": f"Use at most {MAX_CONDITIONS} conditions."})
    return conditions


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


async def save_rule(
    *,
    rule_id: int | None,
    company_id: int,
    form: Mapping[str, Any],
    conditions: list[dict[str, str]],
    user_id: int | None,
    is_super_admin: bool,
) -> int:
    """Create or update a rule from the rules page. ``company_id`` is the
    company the technician is working in."""

    errors: dict[str, str] = {}
    existing = await rules_repo.get_rule(rule_id, with_secrets=True) if rule_id is not None else None
    name = str(form.get("name") or "").strip()[:255]
    if not name:
        errors["name"] = "Give the rule a name."
    scope = str(form.get("scope") or "company")
    rule_company_id: int | None = company_id
    asset_id: int | None = None
    if scope == "global":
        if not is_super_admin:
            errors["scope"] = "Only super admins can add rules for every company."
        rule_company_id = None
    elif scope == "asset":
        try:
            asset_id = int(str(form.get("asset_id") or ""))
        except ValueError:
            asset_id = None
        asset = await assets_repo.get_asset_by_id(asset_id) if asset_id else None
        if not asset or int(asset.get("company_id") or 0) != int(company_id):
            errors["asset_id"] = "Choose a device in this company."
    elif scope != "company":
        errors["scope"] = "Choose who the rule applies to."
    action = str(form.get("action") or "")
    if action not in ACTIONS:
        errors["action"] = "Choose what the rule does."

    script: dict[str, Any] | None = None
    entries: dict[str, str] = {}
    schedule: dict[str, Any] = {"schedule_mode": None, "delay_minutes": None, "run_time": None,
                                 "schedule_timezone": None}
    if action == "schedule_script":
        mode = str(form.get("schedule_mode") or "delay")
        if mode not in SCHEDULE_MODES:
            errors["schedule_mode"] = "Choose when the script runs."
        schedule["schedule_mode"] = mode
        if mode == "delay":
            schedule["delay_minutes"] = _int(form.get("delay_minutes"), DEFAULT_DELAY_MINUTES, 1, MAX_DELAY_MINUTES)
        else:
            run_time = str(form.get("run_time") or "").strip()[:5]
            if not _RUN_TIME.match(run_time):
                errors["run_time"] = "Enter the time as HH:MM, such as 02:00."
            schedule["run_time"] = run_time
            try:
                schedule["schedule_timezone"] = rmm_automation.validate_timezone(form.get("schedule_timezone"))
            except RunRequestError as exc:
                errors.update(exc.errors)
    if action in SCRIPT_ACTIONS:
        try:
            script = await rmm_automation._scoped_script(form.get("script_id"), rule_company_id)
        except RunRequestError as exc:
            errors.update(exc.errors)
        if script is not None:
            same_script = bool(existing) and existing.get("script_id") == script["id"]
            previous = rmm_automation.decrypt_entries(existing.get("entries_encrypted")) if same_script else {}
            # A newly chosen script's fields appear after saving; only the
            # current script's values are read from the form.
            if same_script:
                for field_def in rmm_scripts.script_fields(script):
                    key = field_def["key"]
                    text = str(form.get("entry:" + key) or "")[:65535]
                    if not text.strip() and field_def["sensitive"] and previous.get(key):
                        text = previous[key]  # a blank secret keeps the saved one
                    if text.strip():
                        entries[key] = text
    if errors:
        raise RuleError(errors)

    values = {
        "name": name,
        "company_id": rule_company_id,
        "asset_id": asset_id,
        "action": action,
        "conditions": conditions,
        "script_id": int(script["id"]) if script else None,
        "entries_encrypted": encrypt_secret(json.dumps(entries)) if entries else None,
        "inputs": rmm_scripts.stored_inputs(script, entries) if script else [],
        "timeout_seconds": rmm_scripts.clamp_timeout(
            form.get("timeout_seconds"), int((script or {}).get("default_timeout_seconds") or 600)
        ),
        "cooldown_minutes": _int(form.get("cooldown_minutes"), DEFAULT_COOLDOWN_MINUTES, 0, MAX_COOLDOWN_MINUTES),
        "is_enabled": str(form.get("is_enabled") or "").lower() in {"1", "on", "true", "yes"},
        "user_id": user_id,
        **schedule,
    }
    if existing is None:
        return await rules_repo.create_rule(values)
    await rules_repo.update_rule(int(existing["id"]), values)
    return int(existing["id"])


def script_values(rule: Mapping[str, Any] | None, script: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The rule's script fields with saved values; secrets are never sent back."""

    if not rule or not script:
        return []
    entries = rmm_automation.decrypt_entries(rule.get("entries_encrypted"))
    fields = []
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        secret = bool(field_def["sensitive"]) and not rmm_scripts._TOKEN.search(value)
        fields.append({**field_def, "value": "" if secret else value, "value_set": bool(value)})
    return fields


def can_edit(rule: Mapping[str, Any], *, company_id: int, is_super_admin: bool) -> bool:
    if rule.get("company_id") is None:
        return is_super_admin
    return int(rule["company_id"]) == int(company_id)


# --------------------------------------------------------------------------- #
# Applying rules to incoming entries
# --------------------------------------------------------------------------- #


def event_context(entry: Mapping[str, Any]) -> dict[str, Any]:
    created = entry.get("time_created")
    return {"event": {
        "log_name": entry.get("log_name") or "",
        "event_id": entry.get("event_id"),
        "level": {1: "Critical", 2: "Error", 3: "Warning", 4: "Information", 5: "Verbose"}.get(_entry_level(entry), "Information"),
        "provider": entry.get("provider") or "",
        "computer": entry.get("computer") or "",
        "message": entry.get("message") or "",
        "record_id": entry.get("record_id"),
        "time_created": created.isoformat() if isinstance(created, datetime) else str(created or ""),
    }}


async def _run_rule(rule: Mapping[str, Any], agent: Mapping[str, Any], entry: Mapping[str, Any], matched: int) -> int | None:
    rule_id = int(rule["id"])
    agent_id = int(agent["id"])
    now = _utcnow()
    cooldown = timedelta(minutes=int(rule.get("cooldown_minutes") or 0))
    last = await rules_repo.last_triggered(rule_id, agent_id)
    if last is not None and cooldown and now - last < cooldown:
        await rules_repo.record_matches(rule_id, matched)
        return None
    run_id, summary, attempted = await rmm_actions.run_script(
        script_id=rule.get("script_id"),
        entries_encrypted=rule.get("entries_encrypted"),
        inputs=rule.get("inputs"),
        timeout_seconds=rule.get("timeout_seconds"),
        agent=agent,
        run_source=RUN_SOURCE,
        extra_context=event_context(entry),
    )
    if not attempted:
        await rules_repo.record_matches(rule_id, matched, summary=summary)
        return None
    if run_id is not None:
        summary += f" (event {entry.get('event_id')} in {entry.get('log_name')})."
    await rules_repo.record_trigger(rule_id, agent_id, run_id)
    await rules_repo.record_matches(rule_id, matched, summary=summary)
    log_info("RMM event log rule matched", rule_id=rule_id, agent_id=agent_id, run_id=run_id)
    return run_id


def _entry_html(entry: Mapping[str, Any]) -> str:
    event = event_context(entry)["event"]
    details = "<br>".join(
        f"<strong>{html.escape(label)}:</strong> {html.escape(str(event.get(key) or '–'))}"
        for label, key in (("Log", "log_name"), ("Event ID", "event_id"), ("Level", "level"), ("Source", "provider"),
                           ("Computer", "computer"), ("Time (UTC)", "time_created"))
    )
    message = str(entry.get("message") or "").strip()
    body = f"<p>{details}</p>"
    if message:
        body += f"<pre>{html.escape(message[:4000])}</pre>"
    return body


async def _ticket_rule(rule: Mapping[str, Any], agent: Mapping[str, Any], matched: list[dict[str, Any]]) -> tuple[int | None, bool]:
    """Raise a ticket for the device, or update the one still open.

    Returns the ticket ID and whether it is new."""

    rule_id = int(rule["id"])
    agent_id = int(agent["id"])
    device = rmm_actions.device_label(agent)
    count = len(matched)
    listed = "".join(_entry_html(entry) for entry in matched[:TICKET_EVENTS_LISTED])
    if count > TICKET_EVENTS_LISTED:
        listed += f"<p>…and {count - TICKET_EVENTS_LISTED} more.</p>"
    reference = f"rmm-event-log-rule:{rule_id}:{agent_id}"

    ticket = await rmm_actions.open_ticket(await rules_repo.get_rule_ticket(rule_id, agent_id))
    if ticket:
        await rmm_actions.add_ticket_update(
            ticket,
            body=(f"<p>Event log rule <strong>{html.escape(str(rule['name']))}</strong> matched "
                  f"{count} more event{'s' if count != 1 else ''} on {html.escape(str(device))}.</p>{listed}"),
            reference=reference,
        )
        summary = f"Updated ticket {rmm_actions.ticket_label(ticket)} for {device}."
        await rules_repo.record_matches(rule_id, count, summary=summary)
        return int(ticket["id"]), False

    created = await rmm_actions.raise_ticket(
        agent=agent,
        subject=f"{rule['name']}: {device}",
        description=(f"<p>Event log rule <strong>{html.escape(str(rule['name']))}</strong> matched "
                     f"{count} event{'s' if count != 1 else ''} on {html.escape(str(device))}.</p>{listed}"),
        reference=reference,
    )
    new_id = int(created["id"])
    await rules_repo.set_rule_ticket(rule_id, agent_id, new_id)
    summary = f"Raised ticket {rmm_actions.ticket_label(created)} for {device}."
    await rules_repo.record_matches(rule_id, count, summary=summary)
    log_info("RMM event log rule raised a ticket", rule_id=rule_id, agent_id=agent_id, ticket_id=new_id)
    return new_id, True


def describe_schedule(rule: Mapping[str, Any]) -> str:
    if rule.get("schedule_mode") == "time":
        return f"at {rule.get('run_time')} ({rule.get('schedule_timezone') or 'UTC'})"
    minutes = int(rule.get("delay_minutes") or DEFAULT_DELAY_MINUTES)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''} later"
    return f"{minutes} minute{'s' if minutes != 1 else ''} later"


def scheduled_time(rule: Mapping[str, Any], now: datetime | None = None) -> datetime:
    """When a schedule-a-script rule matched at ``now`` runs (naive UTC)."""

    now = now or _utcnow()
    if rule.get("schedule_mode") != "time":
        return now + timedelta(minutes=int(rule.get("delay_minutes") or DEFAULT_DELAY_MINUTES))
    zone = ZoneInfo(str(rule.get("schedule_timezone") or "UTC"))
    hour, minute = (int(part) for part in str(rule.get("run_time") or "00:00").split(":"))
    local_now = now.replace(tzinfo=timezone.utc).astimezone(zone)
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        target = (local_now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Re-attach the zone so a daylight saving change on the way is respected.
    target = datetime(target.year, target.month, target.day, hour, minute, tzinfo=zone)
    return target.astimezone(timezone.utc).replace(tzinfo=None)


async def _schedule_rule(rule: Mapping[str, Any], agent: Mapping[str, Any], entry: Mapping[str, Any],
                         matched: int) -> bool:
    """Schedule the rule's script on the device; one run waits per rule and device."""

    rule_id = int(rule["id"])
    agent_id = int(agent["id"])
    device = agent.get("hostname") or f"device {agent.get('asset_id') or agent_id}"
    pending = await rules_repo.get_pending(rule_id, agent_id)
    if pending:
        await rules_repo.record_matches(rule_id, matched)
        return False
    run_at = scheduled_time(rule)
    event = event_context(entry)
    await rules_repo.add_pending(rule_id, agent_id, run_at, encrypt_secret(json.dumps(event)))
    name = rule.get("script_name") or "the script"
    await rules_repo.record_matches(
        rule_id, matched, summary=f"Scheduled {name} on {device} for {run_at.isoformat(timespec='minutes')} UTC."
    )
    log_info("RMM event log rule scheduled a script", rule_id=rule_id, agent_id=agent_id, run_at=run_at.isoformat())
    return True


async def run_due_scheduled(now: datetime | None = None) -> int:
    """Queue the scheduled runs that are due. Returns how many were queued."""

    queued = 0
    for pending in await rules_repo.due_pending(now or _utcnow()):
        rule_id, agent_id = int(pending["rule_id"]), int(pending["agent_id"])
        if not await rules_repo.claim_pending(rule_id, agent_id):
            continue  # another instance took it
        try:
            rule = await rules_repo.get_rule(rule_id, with_secrets=True)
            agent = await rmm_repo.get_agent(agent_id)
            if not rule or not rule.get("is_enabled") or rule.get("action") != "schedule_script" or not agent:
                continue  # turned off, changed or removed since it was scheduled
            try:
                event = json.loads(decrypt_secret(str(pending.get("event_encrypted") or ""), allow_plaintext=False))
            except (ValueError, TypeError):
                event = {}
            run_id, summary, attempted = await rmm_actions.run_script(
                script_id=rule.get("script_id"),
                entries_encrypted=rule.get("entries_encrypted"),
                inputs=rule.get("inputs"),
                timeout_seconds=rule.get("timeout_seconds"),
                agent=agent,
                run_source=RUN_SOURCE,
                extra_context=event if isinstance(event, dict) else {},
            )
            if run_id is not None:
                queued += 1
                await rules_repo.record_trigger(rule_id, agent_id, run_id)
                summary += " as scheduled."
            await rules_repo.set_summary(rule_id, summary)
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the others
            log_error("RMM event log scheduled run failed", rule_id=rule_id, agent_id=agent_id, error=str(exc))
    return queued


async def apply(agent: Mapping[str, Any], entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return the entries to store and counts of what the rules did."""

    stats = {"dropped": 0, "runs": 0, "scheduled": 0, "tickets": 0}
    if not entries:
        return entries, stats
    company_id = agent.get("company_id")
    asset_id = agent.get("asset_id")
    rules = await rules_repo.list_rules_for_device(
        int(company_id) if company_id is not None else None,
        int(asset_id) if asset_id is not None else None,
        enabled_only=True,
        with_secrets=True,
    )
    if not rules:
        return entries, stats
    drop_rules = [rule for rule in rules if rule["action"] == "drop"]
    act_rules = [rule for rule in rules if rule["action"] in (*SCRIPT_ACTIONS, "create_ticket")]
    drop_counts: dict[int, int] = {}
    act_matches: dict[int, list[dict[str, Any]]] = {}
    kept: list[dict[str, Any]] = []
    for entry in entries:
        dropped_by = next((rule for rule in drop_rules if rule_matches(rule, entry)), None)
        if dropped_by is not None:
            drop_counts[int(dropped_by["id"])] = drop_counts.get(int(dropped_by["id"]), 0) + 1
            continue
        kept.append(entry)
        for rule in act_rules:
            if rule_matches(rule, entry):
                act_matches.setdefault(int(rule["id"]), []).append(entry)
    for rule_id, count in drop_counts.items():
        await rules_repo.record_matches(rule_id, count)
    stats["dropped"] = sum(drop_counts.values())
    for rule in act_rules:
        matched = act_matches.get(int(rule["id"]))
        if not matched:
            continue
        try:
            if rule["action"] == "create_ticket":
                _ticket_id, is_new = await _ticket_rule(rule, agent, matched)
                stats["tickets"] += 1 if is_new else 0
            elif rule["action"] == "schedule_script":
                if await _schedule_rule(rule, agent, matched[0], len(matched)):
                    stats["scheduled"] += 1
            elif await _run_rule(rule, agent, matched[0], len(matched)):
                stats["runs"] += 1
        except Exception as exc:  # noqa: BLE001 - a rule must never lose the batch
            log_error("RMM event log rule failed", rule_id=rule["id"], error=str(exc))
    return kept, stats


# --------------------------------------------------------------------------- #
# Running a drop rule against stored entries
# --------------------------------------------------------------------------- #

RUN_NOW_BATCH = 2000


def _event_id_prefilter(rule: Mapping[str, Any]) -> list[int] | None:
    """Event IDs every match must have, so the database can narrow the scan."""

    for condition in rule.get("conditions") or []:
        if condition.get("field") != "event_id" or condition.get("operator") not in ("is", "one_of"):
            continue
        value = str(condition.get("value") or "")
        values = _split(value) if condition.get("operator") == "one_of" else [value.strip()]
        if values and all(item.isdigit() for item in values):
            return sorted({int(item) for item in values})
    return None


async def drop_stored(rule: Mapping[str, Any], *, delete: bool) -> int:
    """Count, or delete, the stored entries a drop rule matches in its scope."""

    if rule.get("action") != "drop":
        raise RuleError({"action": "Only drop rules can run now."})
    if not rule.get("conditions"):
        return 0
    company_id = int(rule["company_id"]) if rule.get("company_id") is not None else None
    asset_id = int(rule["asset_id"]) if rule.get("asset_id") is not None else None
    event_ids = _event_id_prefilter(rule)
    matched = 0
    after_id = 0
    while True:
        rows = await event_log_repo.scan_entries(
            company_id=company_id, asset_id=asset_id, event_ids=event_ids, after_id=after_id, limit=RUN_NOW_BATCH,
        )
        if not rows:
            break
        after_id = int(rows[-1]["id"])
        ids = [int(row["id"]) for row in rows if rule_matches(rule, row)]
        matched += len(ids)
        if delete and ids:
            await event_log_repo.delete_entries(ids)
        if len(rows) < RUN_NOW_BATCH:
            break
    if delete:
        await rules_repo.record_matches(
            int(rule["id"]), matched, summary=f"Run now deleted {matched} stored event{'s' if matched != 1 else ''}."
        )
        log_info("RMM event log drop rule run now", rule_id=rule["id"], deleted=matched)
    return matched
