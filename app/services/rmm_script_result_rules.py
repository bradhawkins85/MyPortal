"""Script result rules: act on what a script reports when it finishes.

A rule watches one Gitea script, or every script, for every company (super
admins only) or one company. As each run of that script finishes
(:func:`apply`, called by :func:`rmm_scripts.record_result`), its conditions
(:data:`FIELDS`, :data:`OPERATORS`) are compared with the run's output,
error output, exit code, result and the values the script returned. With
``match_mode`` ``all`` every condition must match, with ``any`` at least one.

When a rule matches it either:

* **creates a ticket** for the device's company with the device linked and
  the result in the description. With ``ticket_mode`` ``update`` later
  matches on the same device add an internal update while that ticket is not
  closed; ``new`` raises a ticket every time; or
* **runs another script** on the same device, as an ordinary RMM run
  (``run_source = 'result_rule'``). Its values may use ``{{result.*}}``
  variables (:data:`RESULT_VARIABLES`), and with ``pass_results`` the result
  also reaches it as ``MYPORTAL_PREVIOUS_*`` environment variables
  (:func:`previous_env`). A follow-up run records the run and rule that
  queued it, and chains stop after :data:`MAX_CHAIN_DEPTH` follow-ups so
  rules cannot run each other forever.

Text comparisons ignore case. Regular expressions use the ``regex`` module
(installed with tiktoken and pinned in requirements.lock) with a time limit,
so a slow pattern cannot hold up the portal.
"""

from __future__ import annotations

import html
import json
from typing import Any, Iterable, Mapping

import regex

from app.core.logging import log_error, log_info
from app.repositories import rmm_script_result_rules as rules_repo
from app.security.encryption import encrypt_secret
from app.services import rmm_actions, rmm_automation, rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "result_rule"
ACTIONS = {
    "create_ticket": "Create a ticket",
    "run_script": "Run another script",
}
MATCH_MODES = {"all": "all of these", "any": "any of these"}
TICKET_MODES = {
    "update": "Update the open ticket for the device",
    "new": "Raise a new ticket every time",
}
TICKET_PRIORITIES = {"low": "Low", "normal": "Normal", "high": "High", "urgent": "Urgent"}
FIELDS = {
    "output": "Output",
    "errors": "Error output",
    "any_output": "Output or error output",
    "exit_code": "Exit code",
    "status": "Result",
    "value": "Returned value",
}
OPERATORS = {
    "contains": "contains",
    "not_contains": "does not contain",
    "is": "is",
    "is_not": "is not",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "one_of": "is one of",
    "not_one_of": "is not one of",
    "matches": "matches the regex",
    "not_matches": "does not match the regex",
    "lt": "is less than",
    "le": "is at most",
    "gt": "is greater than",
    "ge": "is at least",
    "empty": "is empty",
    "not_empty": "is not empty",
}
_TEXT_OPERATORS = ("contains", "not_contains", "is", "is_not", "starts_with", "ends_with", "one_of", "not_one_of",
                   "matches", "not_matches", "lt", "le", "gt", "ge", "empty", "not_empty")
_NUMBER_OPERATORS = ("lt", "le", "gt", "ge")
_REGEX_OPERATORS = ("matches", "not_matches")
_VALUELESS_OPERATORS = ("empty", "not_empty")
FIELD_OPERATORS = {
    "output": _TEXT_OPERATORS,
    "errors": _TEXT_OPERATORS,
    "any_output": _TEXT_OPERATORS,
    "value": _TEXT_OPERATORS,
    "exit_code": ("is", "is_not", "one_of", "not_one_of", "lt", "le", "gt", "ge"),
    "status": ("is", "is_not", "one_of", "not_one_of"),
}
STATUS_LABELS = {"completed": "Completed", "failed": "Failed", "timed_out": "Timed out"}
_STATUS_NAMES = {
    "completed": "completed", "succeeded": "completed", "success": "completed",
    "failed": "failed", "failure": "failed",
    "timed_out": "timed_out", "timed out": "timed_out", "timeout": "timed_out",
}
RESULT_VARIABLES = (
    ("Output", "{{result.output}}"),
    ("Error output", "{{result.errors}}"),
    ("Exit code", "{{result.exit_code}}"),
    ("Result", "{{result.status}}"),
    ("Script", "{{result.script}}"),
    ("Run ID", "{{result.run_id}}"),
    ("Regex match", "{{result.match}}"),
    ("Regex group 1", "{{result.groups.1}}"),
    ("Returned value", "{{result.values.NAME}}"),
)
# Runs MyPortal starts for its own jobs, not technicians' scripts.
BUILT_IN_SOURCES = frozenset({"remote_control", "patch", "update_scan", "tray_repair", "agent_uninstall", "command"})
MAX_CONDITIONS = 10
MAX_VALUE_LENGTH = 1024
MAX_PATTERN_LENGTH = 500
REGEX_TIMEOUT_SECONDS = 0.5
# Text searched by regular expressions and number comparisons.
MAX_MATCH_CHARS = 200_000
MAX_CHAIN_DEPTH = 5
# Result text handed to a follow-up script, as variables or environment
# variables; Windows limits one environment variable to 32,767 characters.
MAX_PASSED_CHARS = 8_000
MAX_TICKET_OUTPUT_CHARS = 20_000
_FIRST_NUMBER = regex.compile(r"[-+]?\d+(?:[.,]\d+)?")
_LIST_SPLIT = regex.compile(r"[,\n]")
_REGEX_FLAGS = regex.IGNORECASE | regex.MULTILINE | regex.VERSION0


class RuleError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _split(value: str) -> list[str]:
    return [item.strip() for item in _LIST_SPLIT.split(value) if item.strip()]


def _number(text: Any) -> float | None:
    """The value as a number: the whole trimmed text, else the first number in it."""

    if text is None:
        return None
    cleaned = str(text).strip()
    try:
        return float(cleaned)
    except ValueError:
        pass
    found = _FIRST_NUMBER.search(cleaned[:MAX_MATCH_CHARS])
    if not found:
        return None
    try:
        return float(found.group(0).replace(",", "."))
    except ValueError:
        return None


def _compare_numbers(actual: float | None, operator: str, wanted: float | None) -> bool:
    if actual is None or wanted is None:
        return False
    return {
        "lt": actual < wanted, "le": actual <= wanted, "gt": actual > wanted, "ge": actual >= wanted,
    }.get(operator, False)


def compile_pattern(pattern: str) -> Any:
    """Compile a rule's regular expression, or raise ``regex.error``."""

    return regex.compile(pattern, _REGEX_FLAGS)


def _regex_search(pattern: str, text: str) -> Any:
    """The first match, or ``None``; a bad or slow pattern matches nothing."""

    try:
        return compile_pattern(pattern).search(text[:MAX_MATCH_CHARS], timeout=REGEX_TIMEOUT_SECONDS)
    except (regex.error, TimeoutError):
        return None


def _status_name(value: str) -> str:
    text = value.strip().casefold()
    return _STATUS_NAMES.get(text, text)


def _returned_value(result: Mapping[str, Any], name: str) -> str | None:
    values = result.get("values") or {}
    lowered = name.strip().casefold()
    for key, value in values.items():
        if str(key).casefold() == lowered:
            return "" if value is None else str(value)
    return None


def _field_text(condition: Mapping[str, Any], result: Mapping[str, Any]) -> str | None:
    field = condition.get("field")
    if field == "output":
        return str(result.get("output") or "")
    if field == "errors":
        return str(result.get("errors") or "")
    if field == "any_output":
        return "\n".join(part for part in (str(result.get("output") or ""), str(result.get("errors") or "")) if part)
    if field == "value":
        return _returned_value(result, str(condition.get("name") or ""))
    return None


def condition_matches(condition: Mapping[str, Any], result: Mapping[str, Any],
                      found: dict[str, Any] | None = None) -> bool:
    """Whether one condition matches a finished run's ``result``.

    ``found`` collects the first regular expression match, for the
    ``{{result.match}}`` and ``{{result.groups.*}}`` variables."""

    field = condition.get("field")
    operator = str(condition.get("operator") or "")
    value = str(condition.get("value") or "")
    if field == "exit_code":
        code = result.get("exit_code")
        if operator in _NUMBER_OPERATORS:
            return _compare_numbers(None if code is None else float(code), operator, _number(value))
        wanted = _split(value) if operator in ("one_of", "not_one_of") else [value.strip()]
        present = code is not None and str(int(code)) in wanted
        return present if operator in ("is", "one_of") else not present
    if field == "status":
        actual = str(result.get("status") or "")
        wanted = [_status_name(item) for item in (_split(value) if operator in ("one_of", "not_one_of") else [value])]
        present = actual in wanted
        return present if operator in ("is", "one_of") else not present

    text = _field_text(condition, result)
    if operator in _VALUELESS_OPERATORS:
        empty = text is None or not text.strip()
        return empty if operator == "empty" else not empty
    if text is None:
        # A value the script did not return only matches "is not" style checks.
        return operator in ("not_contains", "is_not", "not_one_of", "not_matches")
    if operator in _NUMBER_OPERATORS:
        return _compare_numbers(_number(text), operator, _number(value))
    if operator in _REGEX_OPERATORS:
        match = _regex_search(value, text)
        if match is not None and found is not None and "match" not in found:
            found["match"] = match.group(0)
            groups = {str(index): group or "" for index, group in enumerate(match.groups(), start=1)}
            groups.update({name: group or "" for name, group in match.groupdict().items()})
            found["groups"] = groups
        return (match is not None) == (operator == "matches")
    actual = text.casefold()
    target = value.casefold()
    if operator == "contains":
        return target in actual
    if operator == "not_contains":
        return target not in actual
    if operator == "is":
        return actual.strip() == target.strip()
    if operator == "is_not":
        return actual.strip() != target.strip()
    if operator == "starts_with":
        return actual.lstrip().startswith(target)
    if operator == "ends_with":
        return actual.rstrip().endswith(target)
    if operator in ("one_of", "not_one_of"):
        present = actual.strip() in {item.casefold() for item in _split(value)}
        return present if operator == "one_of" else not present
    return False


def rule_matches(rule: Mapping[str, Any], result: Mapping[str, Any], found: dict[str, Any] | None = None) -> bool:
    conditions = rule.get("conditions") or []
    if not conditions:
        return False
    if rule.get("match_mode") == "any":
        # Every condition is checked so a later regex can still fill ``found``.
        return any([condition_matches(condition, result, found) for condition in conditions])
    return all(condition_matches(condition, result, found) for condition in conditions)


def describe_condition(condition: Mapping[str, Any]) -> str:
    field = str(condition.get("field"))
    label = FIELDS.get(field, field)
    if field == "value":
        label = f"Returned value “{condition.get('name')}”"
    parts = [label, OPERATORS.get(str(condition.get("operator")), str(condition.get("operator")))]
    if condition.get("operator") not in _VALUELESS_OPERATORS:
        parts.append(f"“{condition.get('value')}”")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def parse_conditions(fields: Iterable[Any], names: Iterable[Any], operators: Iterable[Any],
                     values: Iterable[Any]) -> list[dict[str, str]]:
    """Read the condition rows of the rule form; rows left blank are ignored."""

    conditions: list[dict[str, str]] = []
    errors: dict[str, str] = {}
    for index, (field, name, operator, value) in enumerate(zip(fields, names, operators, values), start=1):
        field, name = str(field or ""), str(name or "").strip()[:255]
        operator, value = str(operator or ""), str(value or "").strip()
        valueless = operator in _VALUELESS_OPERATORS
        if not value and not (valueless and (field != "value" or name)):
            continue
        key = f"condition_{index}"
        if field not in FIELDS:
            errors[key] = "Choose what to check."
            continue
        if operator not in FIELD_OPERATORS[field]:
            errors[key] = f"“{OPERATORS.get(operator, operator)}” does not work with {FIELDS[field]}."
            continue
        if field == "value" and not name:
            errors[key] = "Enter the name of the value the script returns."
            continue
        if valueless:
            value = ""
        elif len(value) > MAX_VALUE_LENGTH:
            errors[key] = "That value is too long."
            continue
        if operator in _REGEX_OPERATORS:
            if len(value) > MAX_PATTERN_LENGTH:
                errors[key] = f"Use a regular expression of at most {MAX_PATTERN_LENGTH} characters."
                continue
            try:
                compile_pattern(value)
            except regex.error as exc:
                errors[key] = f"That is not a valid regular expression ({exc})."
                continue
        if operator in _NUMBER_OPERATORS and _number(value) is None:
            errors[key] = "Enter a number to compare with."
            continue
        if field == "exit_code" and operator not in _NUMBER_OPERATORS:
            items = _split(value) if operator in ("one_of", "not_one_of") else [value]
            if not all(item.lstrip("-").isdigit() for item in items):
                errors[key] = "Exit codes are whole numbers; separate several with commas."
                continue
        if field == "status":
            items = _split(value) if operator in ("one_of", "not_one_of") else [value]
            if not all(_status_name(item) in STATUS_LABELS for item in items):
                errors[key] = "Use Completed, Failed or Timed out."
                continue
        conditions.append({"field": field, "name": name if field == "value" else "", "operator": operator,
                           "value": value})
    if errors:
        raise RuleError(errors)
    if not conditions:
        raise RuleError({"conditions": "Add at least one condition."})
    if len(conditions) > MAX_CONDITIONS:
        raise RuleError({"conditions": f"Use at most {MAX_CONDITIONS} conditions."})
    return conditions


def can_edit(rule: Mapping[str, Any], *, company_id: int, is_super_admin: bool) -> bool:
    if rule.get("company_id") is None:
        return is_super_admin
    return int(rule["company_id"]) == int(company_id)


def _choice(value: Any, options: Mapping[str, str], default: str) -> str:
    text = str(value or "")
    return text if text in options else default


async def _watched_script(value: Any, company_id: int | None) -> dict[str, Any] | None:
    """The script a rule watches; blank means every script."""

    if value in (None, ""):
        return None
    return await rmm_automation._scoped_script(value, company_id)


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
    if scope == "global":
        if not is_super_admin:
            errors["scope"] = "Only super admins can add rules for every company."
        rule_company_id = None
    elif scope != "company":
        errors["scope"] = "Choose who the rule applies to."

    watched: dict[str, Any] | None = None
    try:
        watched = await _watched_script(form.get("script_id"), rule_company_id)
    except RunRequestError as exc:
        errors["script_id"] = "; ".join(exc.errors.values())
    action = str(form.get("action") or "")
    if action not in ACTIONS:
        errors["action"] = "Choose what the rule does."

    next_script: dict[str, Any] | None = None
    entries: dict[str, str] = {}
    if action == "run_script":
        try:
            next_script = await rmm_automation._scoped_script(form.get("next_script_id"), rule_company_id)
        except RunRequestError as exc:
            errors["next_script_id"] = "; ".join(exc.errors.values())
        if next_script is not None:
            same_script = bool(existing) and existing.get("next_script_id") == next_script["id"]
            previous = rmm_automation.decrypt_entries(existing.get("entries_encrypted")) if same_script else {}
            # A newly chosen script's fields appear after saving; only the
            # current script's values are read from the form.
            if same_script:
                for field_def in rmm_scripts.script_fields(next_script):
                    key = field_def["key"]
                    text = str(form.get("entry:" + key) or "")[:65535]
                    if not text.strip() and field_def["sensitive"] and previous.get(key):
                        text = previous[key]  # a blank secret keeps the saved one
                    if text.strip():
                        entries[key] = text
    ticket_subject = str(form.get("ticket_subject") or "").strip()[:255] or None
    if errors:
        raise RuleError(errors)

    values = {
        "name": name,
        "company_id": rule_company_id,
        "script_id": int(watched["id"]) if watched else None,
        "match_mode": _choice(form.get("match_mode"), MATCH_MODES, "all"),
        "conditions": conditions,
        "action": action,
        "next_script_id": int(next_script["id"]) if next_script else None,
        "entries_encrypted": encrypt_secret(json.dumps(entries)) if entries else None,
        "inputs": rmm_scripts.stored_inputs(next_script, entries) if next_script else [],
        "timeout_seconds": rmm_scripts.clamp_timeout(
            form.get("timeout_seconds"), int((next_script or {}).get("default_timeout_seconds") or 600)
        ),
        "pass_results": str(form.get("pass_results") or "").lower() in {"1", "on", "true", "yes"},
        "ticket_mode": _choice(form.get("ticket_mode"), TICKET_MODES, "update"),
        "ticket_subject": ticket_subject if action == "create_ticket" else None,
        "ticket_priority": _choice(form.get("ticket_priority"), TICKET_PRIORITIES, "normal"),
        "is_enabled": str(form.get("is_enabled") or "").lower() in {"1", "on", "true", "yes"},
        "user_id": user_id,
    }
    if existing is None:
        return await rules_repo.create_rule(values)
    await rules_repo.update_rule(int(existing["id"]), values)
    return int(existing["id"])


def script_values(rule: Mapping[str, Any] | None, script: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The next script's fields with saved values; secrets are never sent back."""

    if not rule or not script:
        return []
    entries = rmm_automation.decrypt_entries(rule.get("entries_encrypted"))
    fields = []
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        secret = bool(field_def["sensitive"]) and not rmm_scripts._TOKEN.search(value)
        fields.append({**field_def, "value": "" if secret else value, "value_set": bool(value)})
    return fields


def rule_summary(rule: Mapping[str, Any]) -> dict[str, Any]:
    """A rule as the rules page and a script's page list it."""

    def _iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    action_label = ACTIONS.get(str(rule.get("action")), str(rule.get("action")))
    if rule.get("action") == "run_script":
        action_label = f"Run {rule.get('next_script_name') or 'a removed script'}"
    return {
        **{key: _iso(value) for key, value in rule.items() if key != "entries_encrypted"},
        "scope_label": "Every company" if rule.get("company_id") is None else (rule.get("company_name") or "This company"),
        "script_label": rule.get("script_name") or "Any script",
        "joiner": "or" if rule.get("match_mode") == "any" else "and",
        "conditions_text": [describe_condition(condition) for condition in rule.get("conditions") or []],
        "action_label": action_label,
    }


# --------------------------------------------------------------------------- #
# Acting on finished runs
# --------------------------------------------------------------------------- #


def run_result(run: Mapping[str, Any], *, status: str, exit_code: int | None, stdout: str, stderr: str,
               error: str | None, custom_values: list[dict[str, Any]]) -> dict[str, Any]:
    """What a finished run reported, as rules compare it."""

    return {
        "run_id": int(run["id"]),
        "script": str(run.get("script_name") or ""),
        "status": status,
        "exit_code": exit_code,
        "output": stdout or "",
        "errors": stderr or "",
        "error_message": error or "",
        "values": {str(item["name"]): item.get("value") for item in custom_values
                   if item.get("scope") in ("asset", "company") and item.get("name")},
    }


def _short(text: Any, limit: int = MAX_PASSED_CHARS) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[:limit] + "\n… truncated by MyPortal …"


def result_context(rule: Mapping[str, Any], result: Mapping[str, Any], found: Mapping[str, Any]) -> dict[str, Any]:
    """``{{result.*}}`` variables for the follow-up script and ticket subject."""

    return {
        "result": {
            "run_id": result["run_id"],
            "script": result["script"],
            "status": STATUS_LABELS.get(result["status"], result["status"]),
            "exit_code": "" if result.get("exit_code") is None else str(result["exit_code"]),
            "output": _short(result.get("output")),
            "errors": _short(result.get("errors")),
            "values": {name: "" if value is None else _short(value) for name, value in result["values"].items()},
            "match": _short(found.get("match") or ""),
            "groups": {key: _short(value) for key, value in (found.get("groups") or {}).items()},
        },
        "rule": {"name": str(rule.get("name") or "")},
    }


def previous_env(result: Mapping[str, Any]) -> dict[str, str]:
    """The finished run's result as environment variables for the next script."""

    return {
        "MYPORTAL_PREVIOUS_RUN_ID": str(result["run_id"]),
        "MYPORTAL_PREVIOUS_SCRIPT": result["script"],
        "MYPORTAL_PREVIOUS_STATUS": result["status"],
        "MYPORTAL_PREVIOUS_EXIT_CODE": "" if result.get("exit_code") is None else str(result["exit_code"]),
        "MYPORTAL_PREVIOUS_OUTPUT": _short(result.get("output")),
        "MYPORTAL_PREVIOUS_ERRORS": _short(result.get("errors")),
        "MYPORTAL_PREVIOUS_VALUES": _short(json.dumps(result["values"]), MAX_PASSED_CHARS * 2),
    }


def _pre(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_TICKET_OUTPUT_CHARS:
        text = text[:MAX_TICKET_OUTPUT_CHARS] + "\n… truncated by MyPortal …"
    return f"<pre>{html.escape(text)}</pre>"


def _ticket_html(rule: Mapping[str, Any], agent: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    device = rmm_actions.device_label(agent)
    details = "<br>".join(
        f"<strong>{html.escape(label)}:</strong> {html.escape(value)}"
        for label, value in (
            ("Script", result["script"]),
            ("Device", device),
            ("Result", STATUS_LABELS.get(result["status"], result["status"])),
            ("Exit code", "–" if result.get("exit_code") is None else str(result["exit_code"])),
            ("Run", f"#{result['run_id']}"),
        )
    )
    joiner = " or " if rule.get("match_mode") == "any" else " and "
    when = joiner.join(describe_condition(condition) for condition in rule.get("conditions") or [])
    body = (f"<p>Script result rule <strong>{html.escape(str(rule['name']))}</strong> matched "
            f"({html.escape(when)}).</p><p>{details}</p>")
    if result.get("error_message"):
        body += f"<p><strong>Error:</strong> {html.escape(str(result['error_message']))}</p>"
    if str(result.get("output") or "").strip():
        body += "<p><strong>Output</strong></p>" + _pre(str(result["output"]))
    if str(result.get("errors") or "").strip():
        body += "<p><strong>Error output</strong></p>" + _pre(str(result["errors"]))
    if result["values"]:
        rows = "".join(f"<li><strong>{html.escape(name)}:</strong> {html.escape(str(value))}</li>"
                       for name, value in result["values"].items())
        body += f"<p><strong>Returned values</strong></p><ul>{rows}</ul>"
    return body


async def _ticket_rule(rule: Mapping[str, Any], agent: Mapping[str, Any], result: Mapping[str, Any],
                       found: Mapping[str, Any]) -> str:
    rule_id = int(rule["id"])
    agent_id = int(agent["id"])
    device = rmm_actions.device_label(agent)
    reference = f"rmm-script-result-rule:{rule_id}:{agent_id}"
    body = _ticket_html(rule, agent, result)
    if rule.get("ticket_mode") != "new":
        ticket = await rmm_actions.open_ticket(await rules_repo.get_rule_ticket(rule_id, agent_id))
        if ticket:
            await rmm_actions.add_ticket_update(ticket, body=body, reference=reference)
            return f"Updated ticket {rmm_actions.ticket_label(ticket)} for {device}."
    subject = f"{rule['name']}: {device}"
    if rule.get("ticket_subject"):
        context = await rmm_scripts.build_template_context(int(agent["company_id"]), agent.get("asset_id"))
        context.update(result_context(rule, result, found))
        subject = (await rmm_scripts.render_value(str(rule["ticket_subject"]), context)).strip() or subject
    created = await rmm_actions.raise_ticket(
        agent=agent, subject=subject, description=body, reference=reference,
        priority=str(rule.get("ticket_priority") or "normal"),
    )
    await rules_repo.set_rule_ticket(rule_id, agent_id, int(created["id"]))
    log_info("RMM script result rule raised a ticket", rule_id=rule_id, agent_id=agent_id, ticket_id=created["id"])
    return f"Raised ticket {rmm_actions.ticket_label(created)} for {device}."


async def _script_rule(rule: Mapping[str, Any], agent: Mapping[str, Any], result: Mapping[str, Any],
                       found: Mapping[str, Any]) -> str:
    depth = await rules_repo.chain_depth(result["run_id"], limit=MAX_CHAIN_DEPTH)
    if depth >= MAX_CHAIN_DEPTH:
        return f"Not run: {MAX_CHAIN_DEPTH} result rule scripts already ran one after another."
    run_id, summary, _attempted = await rmm_actions.run_script(
        script_id=rule.get("next_script_id"),
        entries_encrypted=rule.get("entries_encrypted"),
        inputs=rule.get("inputs"),
        timeout_seconds=rule.get("timeout_seconds"),
        agent=agent,
        run_source=RUN_SOURCE,
        extra_context=result_context(rule, result, found),
        extra_env=previous_env(result) if rule.get("pass_results") else None,
    )
    if run_id is not None:
        await rules_repo.set_run_origin(run_id, parent_run_id=result["run_id"], rule_id=int(rule["id"]))
        summary += f" after run #{result['run_id']}."
    return summary


async def apply(agent: Mapping[str, Any], run: Mapping[str, Any], result: Mapping[str, Any]) -> int:
    """Act on the rules a finished run matches. Returns how many matched.

    A failing rule is logged and never stops the others or the run's result."""

    if run.get("run_source") in BUILT_IN_SOURCES or run.get("company_id") is None:
        return 0
    script_id = int(run["script_id"]) if run.get("script_id") is not None else None
    rules = await rules_repo.list_rules_for_run(int(run["company_id"]), script_id)
    device = {**agent, "company_id": int(run["company_id"]), "asset_id": run.get("asset_id")}
    matched = 0
    for rule in rules:
        found: dict[str, Any] = {}
        if not rule_matches(rule, result, found):
            continue
        matched += 1
        try:
            if rule["action"] == "create_ticket":
                summary = await _ticket_rule(rule, device, result, found)
            elif rule["action"] == "run_script":
                summary = await _script_rule(rule, device, result, found)
            else:
                continue
        except Exception as exc:  # noqa: BLE001 - one rule must not stop the others
            log_error("RMM script result rule failed", rule_id=rule["id"], run_id=result["run_id"], error=str(exc))
            summary = "Could not act on the result; see the portal log."
        await rules_repo.record_match(int(rule["id"]), summary)
        log_info("RMM script result rule matched", rule_id=rule["id"], run_id=result["run_id"], summary=summary)
    return matched
