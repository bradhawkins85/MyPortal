"""Event log rules: drop incoming event log entries, or act when one arrives.

A rule applies to every company (super admins only), one company, or one
device. Its conditions compare an entry's fields (:data:`FIELDS`); every
condition must match. A rule either drops the events it matches or takes its
actions in the order they are listed (:mod:`app.services.rmm_action_steps`),
in any combination. When an agent sends a batch (:func:`apply`):

* entries matching an enabled **drop** rule are not stored, and do not start
  any actions;
* for each enabled rule with actions that some stored entry matches, the
  actions run in order for the device that sent it (:func:`_act`):

  * **create a ticket** raises a ticket for the device's company with the
    device's asset linked. While that ticket is not closed, later matches on
    the same device add an internal update to it instead (:func:`_ticket_step`);
  * **run a script** queues a Gitea script on the device as an ordinary RMM
    run (``run_source = 'event_log'``). Its values are saved encrypted like a
    schedule's and may use ``{{event.*}}`` variables (:data:`EVENT_VARIABLES`)
    from the first matching entry. A rule's scripts run at most once per
    device per ``cooldown_minutes``, so a burst of events queues one run each;
  * **schedule a script** queues the script later on that device, after
    ``delay_minutes`` or at the next ``run_time`` in ``schedule_timezone``
    (:func:`_schedule_step`). One run waits per action and device; the RMM
    automation loop queues it when due (:func:`run_due_scheduled`).

A drop rule can also be run now (:func:`drop_stored`), deleting the entries
already stored that it matches.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.core.logging import log_error, log_info
from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.repositories import rmm_event_logs as event_log_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import rmm_action_steps, rmm_actions, rmm_device_targeting
from app.services.rmm_action_steps import (  # noqa: F401 - shared with checks, kept importable here
    DEFAULT_DELAY_MINUTES,
    MAX_DELAY_MINUTES,
    SCHEDULE_MODES,
    describe_schedule,
    parse_schedule,
    scheduled_time,
)

RUN_SOURCE = "event_log"
ACTIONS = {
    "drop": "Drop the event",
    "run_script": "Run a script",
    "schedule_script": "Schedule a script",
    "create_ticket": "Create a ticket",
}
SCRIPT_ACTIONS = ("run_script", "schedule_script")
# What a rule that does not drop events can do, as listed actions in order.
ACTION_TYPES = ("create_ticket", "run_script", "schedule_script")
# The rules form's choice between dropping events and acting on them.
MODES = {"drop": "Drop the event", "actions": "Take actions"}
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
    target_mode = str(form.get("target_mode") or "all")
    if target_mode not in {"all", "rules"}:
        errors["target_mode"] = "Choose all devices in scope or device rules."
    target_rules = rmm_device_targeting.parse_rules(form, errors)
    action = str(form.get("action") or "")
    if action not in ACTIONS and action not in MODES:
        errors["action"] = "Choose what the rule does."
    actions: list[dict[str, Any]] = []
    if action != "drop":
        actions = await rmm_action_steps.parse_steps(
            form, types=ACTION_TYPES, company_id=rule_company_id, existing=rmm_action_steps.steps(existing),
            errors=errors, legacy_action=action,
        )
        if not actions and "action" not in errors and not any(key.startswith("action-") for key in errors):
            errors["actions"] = "Add at least one action, or choose to drop the event."
    if errors:
        raise RuleError(errors)

    # The older single-action columns follow the first action of each kind.
    legacy = rmm_action_steps.legacy_columns(actions, none="drop")
    values = {
        "name": name,
        "company_id": rule_company_id,
        "asset_id": asset_id,
        "action": legacy["action"],
        "conditions": conditions,
        "target_rules": target_rules,
        "script_id": legacy["script_id"],
        "entries_encrypted": legacy["entries_encrypted"],
        "inputs": legacy["inputs"],
        "timeout_seconds": int(legacy["script_timeout_seconds"] or 600),
        "cooldown_minutes": _int(form.get("cooldown_minutes"), DEFAULT_COOLDOWN_MINUTES, 0, MAX_COOLDOWN_MINUTES),
        "is_enabled": str(form.get("is_enabled") or "").lower() in {"1", "on", "true", "yes"},
        "user_id": user_id,
        "schedule_mode": legacy["schedule_mode"],
        "delay_minutes": legacy["delay_minutes"],
        "run_time": legacy["run_time"],
        "schedule_timezone": legacy["schedule_timezone"],
        "actions": actions if action != "drop" else None,
    }
    if existing is None:
        return await rules_repo.create_rule(values)
    await rules_repo.update_rule(int(existing["id"]), values)
    return int(existing["id"])


def script_values(rule: Mapping[str, Any] | None, script: Mapping[str, Any] | None, *,
                  action_key: str | None = None) -> list[dict[str, Any]]:
    """A script action's fields with saved values (the first script action when
    ``action_key`` is not given); secrets are never sent back."""

    if not rule or not script:
        return []
    step = (rmm_action_steps.find(rule, action_key) if action_key
            else next((item for item in rmm_action_steps.steps(rule) if item["type"] in SCRIPT_ACTIONS), None))
    return rmm_action_steps.script_values(step, script)


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


async def _ticket_step(rule: Mapping[str, Any], agent: Mapping[str, Any], matched: list[dict[str, Any]]) -> tuple[str, bool]:
    """Raise a ticket for the device, or update the one still open.

    Returns what it did, in words, and whether the ticket is new."""

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
        return f"Updated ticket {rmm_actions.ticket_label(ticket)} for {device}.", False

    created = await rmm_actions.raise_ticket(
        agent=agent,
        subject=f"{rule['name']}: {device}",
        description=(f"<p>Event log rule <strong>{html.escape(str(rule['name']))}</strong> matched "
                     f"{count} event{'s' if count != 1 else ''} on {html.escape(str(device))}.</p>{listed}"),
        reference=reference,
    )
    new_id = int(created["id"])
    await rules_repo.set_rule_ticket(rule_id, agent_id, new_id)
    log_info("RMM event log rule raised a ticket", rule_id=rule_id, agent_id=agent_id, ticket_id=new_id)
    return f"Raised ticket {rmm_actions.ticket_label(created)} for {device}.", True


async def _schedule_step(rule: Mapping[str, Any], step: Mapping[str, Any], agent: Mapping[str, Any],
                         entry: Mapping[str, Any], now: datetime) -> str | None:
    """Schedule one of the rule's scripts on the device; one run waits per action
    and device. Returns what it did, or None when a run was already waiting."""

    rule_id = int(rule["id"])
    agent_id = int(agent["id"])
    pending = await rules_repo.get_pending(rule_id, agent_id, step["key"])
    first = rmm_action_steps.first_scheduled(rule)
    if not pending and first is not None and first["key"] == step["key"]:
        pending = await rules_repo.get_pending(rule_id, agent_id)  # scheduled by an earlier release
    if pending:
        return None
    run_at = scheduled_time(step, now)
    await rules_repo.add_pending(rule_id, agent_id, run_at, encrypt_secret(json.dumps(event_context(entry))),
                                 action_key=step["key"])
    name = step.get("script_name")
    if not name and step.get("script_id"):
        name = (await rmm_repo.script_names([step["script_id"]])).get(int(step["script_id"]))
    log_info("RMM event log rule scheduled a script", rule_id=rule_id, agent_id=agent_id, run_at=run_at.isoformat(),
             action_key=step["key"])
    return f"Scheduled {name or 'the script'} on {rmm_actions.device_label(agent)} for {run_at.isoformat(timespec='minutes')} UTC."


async def _act(rule: Mapping[str, Any], agent: Mapping[str, Any], matched: list[dict[str, Any]]) -> dict[str, int]:
    """Take a rule's actions in order for the events one device sent.

    Scripts run at most once per ``cooldown_minutes`` on a device; within it the
    matches are only counted. One action failing does not stop the next."""

    rule_id = int(rule["id"])
    agent_id = int(agent["id"])
    now = _utcnow()
    entry = matched[0]
    stats = {"runs": 0, "scheduled": 0, "tickets": 0}
    actions = rmm_action_steps.steps(rule)
    scripts_due = True
    if any(step["type"] == "run_script" for step in actions):
        cooldown = timedelta(minutes=int(rule.get("cooldown_minutes") or 0))
        last = await rules_repo.last_triggered(rule_id, agent_id)
        scripts_due = last is None or not cooldown or now - last >= cooldown
    done: list[str] = []
    ran = False
    last_run_id: int | None = None
    for step in actions:
        try:
            if step["type"] == "create_ticket":
                text, is_new = await _ticket_step(rule, agent, matched)
                done.append(text)
                stats["tickets"] += 1 if is_new else 0
            elif step["type"] == "schedule_script":
                text = await _schedule_step(rule, step, agent, entry, now)
                if text:
                    done.append(text)
                    stats["scheduled"] += 1
            elif step["type"] == "run_script" and scripts_due:
                run_id, text, attempted = await rmm_actions.run_script(
                    script_id=step.get("script_id"),
                    entries_encrypted=step.get("entries_encrypted"),
                    inputs=step.get("inputs"),
                    timeout_seconds=step.get("timeout_seconds"),
                    agent=agent,
                    run_source=RUN_SOURCE,
                    extra_context=event_context(entry),
                )
                ran = ran or attempted
                if run_id is not None:
                    last_run_id = run_id
                    stats["runs"] += 1
                    text += f" (event {entry.get('event_id')} in {entry.get('log_name')})."
                    log_info("RMM event log rule matched", rule_id=rule_id, agent_id=agent_id, run_id=run_id,
                             action_key=step["key"])
                done.append(text)
        except Exception as exc:  # noqa: BLE001 - the next action still runs
            log_error("RMM event log rule action failed", rule_id=rule_id, agent_id=agent_id, action=step["type"],
                      action_key=step["key"], error=str(exc))
            done.append(f"{rmm_action_steps.describe(step)} failed.")
    if ran:
        await rules_repo.record_trigger(rule_id, agent_id, last_run_id)
    await rules_repo.record_matches(rule_id, len(matched), summary=" ".join(done) if done else None)
    return stats


async def run_due_scheduled(now: datetime | None = None) -> int:
    """Queue the scheduled runs that are due. Returns how many were queued."""

    queued = 0
    for pending in await rules_repo.due_pending(now or _utcnow()):
        rule_id, agent_id = int(pending["rule_id"]), int(pending["agent_id"])
        action_key = pending.get("action_key")
        if not await rules_repo.claim_pending(rule_id, agent_id, action_key):
            continue  # another instance took it
        try:
            rule = await rules_repo.get_rule(rule_id, with_secrets=True)
            agent = await rmm_repo.get_agent(agent_id)
            # A run scheduled by an earlier release belongs to the first scheduled script.
            step = (rmm_action_steps.find(rule, action_key) if action_key is not None
                    else rmm_action_steps.first_scheduled(rule))
            if (not rule or not rule.get("is_enabled") or rule.get("action") == "drop" or not step
                    or step["type"] != "schedule_script" or not agent):
                continue  # turned off, changed or removed since it was scheduled
            if rule.get("company_id") is not None and rule["company_id"] != agent.get("company_id"):
                continue
            if rule.get("asset_id") is not None and rule["asset_id"] != agent.get("asset_id"):
                continue
            if rule.get("target_rules") is not None and not await rmm_device_targeting.device_matches(agent, rule["target_rules"]):
                continue
            try:
                event = json.loads(decrypt_secret(str(pending.get("event_encrypted") or ""), allow_plaintext=False))
            except (ValueError, TypeError):
                event = {}
            run_id, summary, attempted = await rmm_actions.run_script(
                script_id=step.get("script_id"),
                entries_encrypted=step.get("entries_encrypted"),
                inputs=step.get("inputs"),
                timeout_seconds=step.get("timeout_seconds"),
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
    if any(rule.get("target_rules") is not None for rule in rules):
        from app.repositories import rmm_targeting as targeting_repo
        from app.services import rmm_targeting
        contexts = await targeting_repo.contexts_for_agents([dict(agent)])
        context = contexts.get(int(agent["id"]))
        eligible = []
        for rule in rules:
            try:
                if rule.get("target_rules") is None or rmm_targeting.compile_rules(rule["target_rules"])(context):
                    eligible.append(rule)
            except (ValueError, TypeError, OverflowError, RecursionError):
                continue
        rules = eligible
    drop_rules = [rule for rule in rules if rule["action"] == "drop"]
    act_rules = [rule for rule in rules if rule["action"] != "drop" and rmm_action_steps.steps(rule)]
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
            done = await _act(rule, agent, matched)
            for key, value in done.items():
                stats[key] += value
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
    """Count, or delete, the stored events a drop rule matches in its scope."""

    if rule.get("action") != "drop":
        raise RuleError({"action": "Only drop rules can run now."})
    if not rule.get("conditions"):
        return 0
    company_id = int(rule["company_id"]) if rule.get("company_id") is not None else None
    asset_id = int(rule["asset_id"]) if rule.get("asset_id") is not None else None
    allowed_agents = None
    if rule.get("target_rules") is not None:
        from app.repositories import rmm_automation as automation_repo
        agents = await automation_repo.list_active_agents(company_id=company_id)
        if asset_id is not None:
            agents = [agent for agent in agents if agent.get("asset_id") == asset_id]
        allowed_agents = {int(agent["id"]) for agent in await rmm_device_targeting.filter_agents(agents, rule["target_rules"])}
        if not allowed_agents:
            return 0
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
        hits = [row for row in rows if (allowed_agents is None or row.get("agent_id") in allowed_agents) and rule_matches(rule, row)]
        ids = [int(row["id"]) for row in hits]
        # An entry can stand for several repeats of its event.
        matched += sum(int(row.get("repeat_count") or 1) for row in hits)
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
