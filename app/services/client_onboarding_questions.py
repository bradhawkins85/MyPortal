"""Configure onboarding questions and validate immutable answer snapshots."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email

from app.repositories import client_onboarding_questions as questions_repo

FIELD_TYPES = {
    "short_text": "Short answer",
    "long_text": "Long answer",
    "email": "Email address",
    "phone": "Phone number",
    "date": "Date picker",
    "dropdown": "Dropdown",
    "multi_select": "Multiple select",
    "checkbox": "Tick box",
    "radio": "Single choice (radio buttons)",
    "number": "Number",
    "url": "Website URL",
    "time": "Time",
    "datetime_local": "Date and time",
    "form": "Custom form (multiple entries)",
}

# Sub-question types a custom form can ask in each entry.
FORM_FIELD_TYPES = {
    "short_text": "Short answer",
    "long_text": "Long answer",
    "email": "Email address",
    "phone": "Phone number",
    "date": "Date",
    "dropdown": "Dropdown",
    "number": "Number",
    "url": "Website URL",
}

SECTIONS = {
    "business": "Your business",
    "sites": "Sites",
    "billing": "Billing",
    "review": "Review",
    "site_address": "address",
    "site_contact": "primary contact",
    "site_hours": "opening hours",
}

MAX_LABEL_LENGTH = 255
MAX_HELP_TEXT_LENGTH = 2000
MAX_SHORT_ANSWER_LENGTH = 1000
MAX_LONG_ANSWER_LENGTH = 10000
MAX_OPTIONS = 100
MAX_OPTION_LENGTH = 255
MAX_DISPLAY_ORDER = 10000
MAX_QUESTIONS = 100
MAX_FORM_FIELDS = 20
MAX_FORM_ENTRIES = 50
MAX_ENTRY_LABEL_LENGTH = 60
DEFAULT_ENTRY_LABEL = "Entry"
CHOICE_TYPES = frozenset({"dropdown", "multi_select", "radio"})
ANSWER_LIMITS = {
    "short_text": MAX_SHORT_ANSWER_LENGTH,
    "long_text": MAX_LONG_ANSWER_LENGTH,
    "email": 255,
    "phone": 50,
    "date": 10,
    "dropdown": MAX_OPTION_LENGTH,
    "multi_select": MAX_OPTION_LENGTH,
    "checkbox": 5,
    "radio": MAX_OPTION_LENGTH,
    "number": 100,
    "url": 2048,
    "time": 8,
    "datetime_local": 19,
}
_ENTRY_TOKEN = re.compile(r"[0-9]{1,6}")

_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})
_FALSE_VALUES = frozenset({"", "0", "false", "off", "no"})
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
# The leading character class already absorbs spaces before "x"/"ext", so
# no separate " *" is needed there (it would allow polynomial backtracking).
_PHONE = re.compile(r"\+?[0-9().\- ]+(?:(?:x|ext\.?) *[0-9]{1,8})?", re.IGNORECASE)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _has_controls(value: str, *, multiline: bool = False) -> bool:
    allowed = {"\n", "\r", "\t"} if multiline else set()
    return any((ord(char) < 32 and char not in allowed) or ord(char) == 127 for char in value)


def _getlist(form: Mapping[str, Any], key: str) -> list[str]:
    if hasattr(form, "getlist"):
        return [_string(value) for value in form.getlist(key)]
    value = form.get(key)
    if value is None:
        return []
    return [_string(item) for item in value] if isinstance(value, (list, tuple)) else [_string(value)]


async def list_questions() -> list[dict[str, Any]]:
    return await questions_repo.list_questions()


def validate_question(form: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate admin settings, rejecting oversize values without truncation."""
    errors: list[str] = []
    label = _string(form.get("label")).strip()
    field_type = _string(form.get("field_type") or "short_text").strip()
    section = _string(form.get("section") or "business").strip()
    help_text = _string(form.get("help_text")).strip()
    if not label:
        errors.append("Enter a question label.")
    elif len(label) > MAX_LABEL_LENGTH or _has_controls(label):
        errors.append(f"Question labels must be a single line of no more than {MAX_LABEL_LENGTH} characters.")
    if field_type not in FIELD_TYPES:
        errors.append("Choose a supported field type.")
    if section not in SECTIONS:
        errors.append("Choose a valid form section.")
    if len(help_text) > MAX_HELP_TEXT_LENGTH or _has_controls(help_text, multiline=True):
        errors.append(f"Help text must contain no more than {MAX_HELP_TEXT_LENGTH} characters.")
    raw_required = _string(form.get("required")).strip().lower()
    if raw_required not in _TRUE_VALUES | _FALSE_VALUES:
        errors.append("Choose whether the question is required.")
    required = raw_required in _TRUE_VALUES
    raw_order = _string(form.get("display_order", 0)).strip() or "0"
    if not re.fullmatch(r"[0-9]{1,5}", raw_order) or int(raw_order) > MAX_DISPLAY_ORDER:
        errors.append(f"Display order must be a whole number between 0 and {MAX_DISPLAY_ORDER}.")
        display_order = 0
    else:
        display_order = int(raw_order)

    raw_options = form.get("options") or []
    if isinstance(raw_options, str):
        raw_options = raw_options.splitlines()
    if not isinstance(raw_options, (list, tuple)):
        errors.append("Enter choice options, one per line.")
        raw_options = []
    options = [_string(option).strip() for option in raw_options if _string(option).strip()]
    if field_type in CHOICE_TYPES:
        if not options:
            errors.append("Add at least one choice option.")
        elif len(options) > MAX_OPTIONS:
            errors.append(f"Add no more than {MAX_OPTIONS} choice options.")
        if any(len(option) > MAX_OPTION_LENGTH or _has_controls(option) for option in options):
            errors.append(f"Each choice option must be a single line of no more than {MAX_OPTION_LENGTH} characters.")
        if len(set(options)) != len(options):
            errors.append("Each choice option must be unique.")
    else:
        options = []
    form_definition = None
    if field_type == "form":
        form_definition, form_errors = validate_form_definition(form)
        errors.extend(form_errors)
    data = {
        "label": label, "field_type": field_type, "section": section,
        "help_text": help_text, "required": required, "options": options,
        "display_order": display_order,
    }
    if form_definition is not None:
        data["form"] = form_definition
    return data, errors


def _options_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    return [_string(option).strip() for option in raw if _string(option).strip()]


def posted_form_definition(form: Mapping[str, Any]) -> dict[str, Any]:
    """Read a custom form's entry name and sub-questions as the admin entered them.

    Accepts either a saved ``form`` mapping or the editor's parallel
    ``form_field_*`` inputs (one value per sub-question row).
    """
    definition = form.get("form")
    if isinstance(definition, Mapping):
        fields = definition.get("fields")
        return {
            "entry_label": _string(definition.get("entry_label")),
            "fields": [dict(item) for item in fields if isinstance(item, Mapping)]
            if isinstance(fields, (list, tuple)) else [],
        }
    labels = _getlist(form, "form_field_label")
    types = _getlist(form, "form_field_type")
    required = _getlist(form, "form_field_required")
    options = _getlist(form, "form_field_options")

    def at(values: list[str], index: int) -> str:
        return values[index] if index < len(values) else ""

    return {
        "entry_label": _string(form.get("form_entry_label")),
        "fields": [
            {
                "label": label, "field_type": at(types, index),
                "required": at(required, index), "options": at(options, index),
            }
            for index, label in enumerate(labels)
        ],
    }


def validate_form_definition(form: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a custom form's sub-questions; blank editor rows are ignored."""
    posted = posted_form_definition(form)
    errors: list[str] = []
    entry_label = posted["entry_label"].strip()
    if len(entry_label) > MAX_ENTRY_LABEL_LENGTH or _has_controls(entry_label):
        errors.append(f"The entry name must be a single line of no more than {MAX_ENTRY_LABEL_LENGTH} characters.")
    fields: list[dict[str, Any]] = []
    for raw in posted["fields"]:
        label = _string(raw.get("label")).strip()
        options = _options_list(raw.get("options"))
        field_type = _string(raw.get("field_type") or "short_text").strip()
        if not label and (not options or field_type != "dropdown"):
            continue
        position = len(fields) + 1
        prefix = f"Form question {position}: "
        if not label:
            errors.append(prefix + "enter a label.")
        elif len(label) > MAX_LABEL_LENGTH or _has_controls(label):
            errors.append(prefix + f"labels must be a single line of no more than {MAX_LABEL_LENGTH} characters.")
        if field_type not in FORM_FIELD_TYPES:
            errors.append(prefix + "choose a supported answer type.")
        raw_required = raw.get("required")
        if isinstance(raw_required, bool):
            required = raw_required
        else:
            text = _string(raw_required).strip().lower()
            if text not in _TRUE_VALUES | _FALSE_VALUES:
                errors.append(prefix + "choose whether an answer is required.")
            required = text in _TRUE_VALUES
        if field_type == "dropdown":
            if not options:
                errors.append(prefix + "add at least one dropdown option.")
            elif len(options) > MAX_OPTIONS:
                errors.append(prefix + f"add no more than {MAX_OPTIONS} dropdown options.")
            if any(len(option) > MAX_OPTION_LENGTH or _has_controls(option) for option in options):
                errors.append(prefix + f"each option must be a single line of no more than {MAX_OPTION_LENGTH} characters.")
            if len(set(options)) != len(options):
                errors.append(prefix + "each option must be unique.")
        else:
            options = []
        fields.append({
            "key": f"f{position}", "label": label, "field_type": field_type,
            "required": required, "options": options,
        })
    if not fields:
        errors.append("Add at least one question to the custom form.")
    elif len(fields) > MAX_FORM_FIELDS:
        errors.append(f"A custom form can ask no more than {MAX_FORM_FIELDS} questions.")
    return {"entry_label": entry_label or DEFAULT_ENTRY_LABEL, "fields": fields}, errors


def form_fields(question: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    definition = question.get("form") if isinstance(question.get("form"), Mapping) else {}
    entry_label = _string(definition.get("entry_label")).strip() or DEFAULT_ENTRY_LABEL
    fields = definition.get("fields")
    if not isinstance(fields, (list, tuple)):
        return entry_label, []
    return entry_label, [
        dict(item) for item in fields
        if isinstance(item, Mapping) and _string(item.get("key")) and item.get("field_type") in FORM_FIELD_TYPES
    ]


def _entry_tokens(form: Mapping[str, Any], name: str) -> tuple[list[str], bool]:
    """Return the posted entry tokens in order and whether they were well formed."""
    raw = _getlist(form, f"{name}-entry")
    tokens = list(dict.fromkeys(raw))
    valid = len(tokens) == len(raw) and all(_ENTRY_TOKEN.fullmatch(token) for token in tokens)
    return (tokens if valid else [token for token in tokens if _ENTRY_TOKEN.fullmatch(token)]), valid


async def save_question(question_id: int | None, data: dict[str, Any]) -> dict[str, Any] | None:
    question, errors = validate_question(data)
    if errors:
        raise ValueError(" ".join(errors))
    return await questions_repo.save_question(question_id, question)


async def delete_question(question_id: int) -> bool:
    return await questions_repo.delete_question(question_id)


def group_questions(questions: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
    for question in questions:
        section = question.get("section")
        if section in groups:
            groups[section].append(dict(question))
    for group in groups.values():
        group.sort(key=lambda item: (int(item.get("display_order") or 0), int(item["id"])))
    return groups


def answer_state(
    form: Mapping[str, Any] | None, questions: Iterable[Mapping[str, Any]], *, prefix: str = "",
) -> dict[str, Any]:
    """Keep original posted values so validation errors never erase answers."""
    state: dict[str, Any] = {}
    for question in questions:
        key = str(question["id"])
        name = f"{prefix}custom-{key}"
        field_type = question["field_type"]
        if field_type == "multi_select":
            state[key] = _getlist(form, name) if form is not None else []
        elif field_type == "form":
            _, fields = form_fields(question)
            tokens = _entry_tokens(form, name)[0][:MAX_FORM_ENTRIES] if form is not None else []
            state[key] = [
                {
                    "token": token,
                    "values": {
                        field["key"]: _string(form.get(f"{name}-{token}-{field['key']}")) for field in fields
                    },
                }
                for token in tokens
            ]
        elif field_type == "checkbox":
            raw = _string(form.get(name)).strip().lower() if form is not None else ""
            state[key] = raw in _TRUE_VALUES if raw in _TRUE_VALUES | _FALSE_VALUES else raw
        else:
            state[key] = _string(form.get(name)) if form is not None else ""
    return state


def _valid_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        if any(char.isspace() for char in value) or "\\" in value:
            return False
        # Accessing port also rejects malformed and out-of-range ports.
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return False
        host = parsed.hostname.encode("idna").decode("ascii")
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return len(host) <= 253 and all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in host.rstrip(".").split(".")
            )
    except (UnicodeError, ValueError):
        return False


def _parse_scalar(field_type: str, value: str) -> tuple[str, str | None]:
    """Return a canonical value and a validation error, if any."""
    if field_type == "email":
        try:
            return validate_email(value, check_deliverability=False, test_environment=True).normalized.lower(), None
        except EmailNotValidError:
            return value, "enter a valid email address."
    if field_type == "phone":
        digits = re.findall(r"[0-9]", value)
        if not _PHONE.fullmatch(value) or not 3 <= len(digits) <= 20:
            return value, "enter a valid phone number."
    elif field_type == "url":
        if not _valid_url(value):
            return value, "enter a valid website URL beginning with http:// or https://."
    elif field_type == "number":
        try:
            number = Decimal(value)
            if not _NUMBER.fullmatch(value) or not number.is_finite() or number.copy_abs() > Decimal("1e100"):
                raise InvalidOperation
            return str(number), None
        except InvalidOperation:
            return value, "enter a valid finite number with an absolute value no greater than 1e100."
    elif field_type == "date":
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError
            return date.fromisoformat(value).isoformat(), None
        except ValueError:
            return value, "enter a valid date."
    elif field_type == "time":
        try:
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value):
                raise ValueError
            parsed = time.fromisoformat(value)
            return parsed.isoformat(timespec="seconds" if len(value) == 8 else "minutes"), None
        except ValueError:
            return value, "enter a valid time."
    elif field_type == "datetime_local":
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value):
                raise ValueError
            parsed = datetime.fromisoformat(value)
            return parsed.isoformat(timespec="seconds" if len(value) == 19 else "minutes"), None
        except ValueError:
            return value, "enter a valid local date and time."
    return value, None


def _parse_text(
    field_type: str, raw_value: str, *, posted_count: int, required: bool, options: list[str],
) -> tuple[str, str, str | None]:
    """Validate one single-value answer; return value, display value and error."""
    value = raw_value.strip()
    error: str | None = None
    if posted_count > 1:
        error = "provide one answer."
    elif len(raw_value) > ANSWER_LIMITS[field_type]:
        error = f"use no more than {ANSWER_LIMITS[field_type]} characters."
    elif _has_controls(value, multiline=field_type == "long_text"):
        error = "enter text without unsupported control characters."
    elif not value:
        if required:
            error = "provide an answer."
    elif field_type in CHOICE_TYPES:
        if value not in options:
            error = "select one of the available choices."
    else:
        value, error = _parse_scalar(field_type, value)
    return value, (value if value else "Not provided"), error


def _parse_form(
    form: Mapping[str, Any], name: str, question: Mapping[str, Any],
) -> tuple[list[list[dict[str, Any]]], str, list[str]]:
    """Validate a custom form's entries; entries left completely blank are dropped."""
    entry_label, fields = form_fields(question)
    tokens, valid = _entry_tokens(form, name)
    if not fields:
        required_error = [f"add at least one {entry_label.lower()}."] if question.get("required") else []
        return [], "Not provided", required_error
    if not valid:
        return [], "Not provided", ["the entries could not be read. Refresh the page and try again."]
    if len(tokens) > MAX_FORM_ENTRIES:
        return [], "Not provided", [f"add no more than {MAX_FORM_ENTRIES} entries."]
    entries: list[list[dict[str, Any]]] = []
    errors: list[str] = []
    for number, token in enumerate(tokens, start=1):
        names = [f"{name}-{token}-{field['key']}" for field in fields]
        if not any(_string(value).strip() for field_name in names for value in _getlist(form, field_name)):
            continue
        entry: list[dict[str, Any]] = []
        for field, field_name in zip(fields, names):
            field_type = str(field["field_type"])
            value, display_value, error = _parse_text(
                field_type, _string(form.get(field_name)),
                posted_count=len(_getlist(form, field_name)),
                required=bool(field.get("required")), options=list(field.get("options") or []),
            )
            if error:
                errors.append(f"{entry_label} {number}: {field['label']}: {error}")
            entry.append({
                "key": str(field["key"]), "label": str(field["label"]), "field_type": field_type,
                "value": value, "display_value": display_value,
            })
        entries.append(entry)
    if question.get("required") and not entries and not errors:
        errors.append(f"add at least one {entry_label.lower()}.")
    display_value = "\n\n".join(
        f"{entry_label} {index}\n" + "\n".join(f"{cell['label']}: {cell['display_value']}" for cell in entry)
        for index, entry in enumerate(entries, start=1)
    ) or "Not provided"
    return entries, display_value, errors


def parse_answers(
    form: Mapping[str, Any], questions: Iterable[Mapping[str, Any]], *, sections: Iterable[str], prefix: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate only this form/site's questions and snapshot labels and values."""
    selected_sections = set(sections)
    answers: list[dict[str, Any]] = []
    errors: list[str] = []
    for question in questions:
        section = str(question["section"])
        if section not in selected_sections:
            continue
        field_type = str(question["field_type"])
        label = str(question["label"])
        error_prefix = f"{SECTIONS.get(section, section)}: {label}: "
        name = f"{prefix}custom-{question['id']}"
        value: Any
        error: str | None = None
        if field_type not in FIELD_TYPES:
            errors.append(error_prefix + "this question has an unsupported field type.")
            continue
        if field_type == "multi_select":
            raw_values = _getlist(form, name)
            options = question.get("options") or []
            # Keep the submitted order, dropping duplicate selections.
            value = list(dict.fromkeys(item for item in raw_values if item))
            if len(raw_values) > MAX_OPTIONS or any(item not in options for item in value):
                error = "select only the available choices."
            elif question.get("required") and not value:
                error = "select at least one option."
            display_value = ", ".join(value) if value else "Not provided"
        elif field_type == "checkbox":
            raw = _string(form.get(name)).strip().lower()
            value = raw in _TRUE_VALUES
            if len(_getlist(form, name)) > 1:
                error = "provide one tick box answer."
            elif raw not in _TRUE_VALUES | _FALSE_VALUES:
                error = "choose a valid tick box value."
            elif question.get("required") and not value:
                error = "tick this box to continue."
            display_value = "Yes" if value else "No"
        elif field_type == "form":
            value, display_value, form_errors = _parse_form(form, name, question)
            if form_errors:
                errors.extend(error_prefix + item for item in form_errors)
                continue
        else:
            value, display_value, error = _parse_text(
                field_type, _string(form.get(name)), posted_count=len(_getlist(form, name)),
                required=bool(question.get("required")), options=list(question.get("options") or []),
            )
        if error:
            errors.append(error_prefix + error)
            continue
        answer = {
            "id": int(question["id"]), "label": label, "field_type": field_type,
            "section": section, "value": value, "display_value": display_value,
        }
        if field_type == "form":
            answer["entry_label"] = form_fields(question)[0]
        answers.append(answer)
    return answers, errors
