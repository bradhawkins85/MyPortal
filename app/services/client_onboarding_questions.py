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

_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})
_FALSE_VALUES = frozenset({"", "0", "false", "off", "no"})
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_PHONE = re.compile(r"\+?[0-9().\- ]+(?: *(?:x|ext\.?) *[0-9]{1,8})?", re.IGNORECASE)


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
    return {
        "label": label, "field_type": field_type, "section": section,
        "help_text": help_text, "required": required, "options": options,
        "display_order": display_order,
    }, errors


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
        else:
            posted = _getlist(form, name)
            raw_value = _string(form.get(name))
            value = raw_value.strip()
            if len(posted) > 1:
                error = "provide one answer."
            elif len(raw_value) > ANSWER_LIMITS[field_type]:
                error = f"use no more than {ANSWER_LIMITS[field_type]} characters."
            elif _has_controls(value, multiline=field_type == "long_text"):
                error = "enter text without unsupported control characters."
            elif not value:
                if question.get("required"):
                    error = "provide an answer."
            elif field_type in CHOICE_TYPES:
                if value not in (question.get("options") or []):
                    error = "select one of the available choices."
            else:
                value, error = _parse_scalar(field_type, value)
            display_value = value if value else "Not provided"
        if error:
            errors.append(error_prefix + error)
            continue
        answers.append({
            "id": int(question["id"]), "label": label, "field_type": field_type,
            "section": section, "value": value, "display_value": display_value,
        })
    return answers, errors
