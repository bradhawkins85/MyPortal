"""Matrix chat auto-assign service.

Evaluates configured rules against a new chat room's context and, when a rule
matches, assigns the corresponding technician to the room.

Rule evaluation order:
1. Active rules are evaluated in priority order (highest first).
2. All conditions in a rule must match (AND logic).
3. The first matching rule wins.
4. If no rule matches, the default fallback rule is applied (if one exists and
   has an assigned technician).

Supported condition types:
- ``company_name``   – the name of the company that owns the chat room.
- ``contact_name``   – the display name of the user who created the room.
- ``subject``        – the room subject/title.
- ``time_between``   – current UTC time is within a HH:MM-HH:MM window.
- ``day_of_week``    – today is one of the listed weekday short names (mon,tue,...).

Supported operators (where applicable):
- ``contains``     – case-insensitive substring match.
- ``equals``       – case-insensitive exact match.
- ``starts_with``  – case-insensitive prefix match.
- ``between``      – time range "HH:MM-HH:MM" (for ``time_between`` conditions).
- ``in``           – comma-separated list membership (for ``day_of_week``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import log_info, log_error
from app.repositories import chat_auto_assign as repo


_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _match_string(operator: str, field_value: str, condition_value: str) -> bool:
    """Evaluate a string condition against a field value."""
    fv = (field_value or "").lower()
    cv = (condition_value or "").lower()
    if operator == "contains":
        return cv in fv
    if operator == "equals":
        return fv == cv
    if operator == "starts_with":
        return fv.startswith(cv)
    return False


def _match_time_between(condition_value: str) -> bool:
    """Return True if the current UTC time falls within the HH:MM-HH:MM range."""
    try:
        start_str, end_str = condition_value.split("-", 1)
        start_parts = start_str.split(":")
        end_parts = end_str.split(":")
        sh, sm = int(start_parts[0]), int(start_parts[1])
        eh, em = int(end_parts[0]), int(end_parts[1])
        now = datetime.now(timezone.utc)
        current_minutes = now.hour * 60 + now.minute
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes <= end_minutes
        # Wraps midnight
        return current_minutes >= start_minutes or current_minutes <= end_minutes
    except (ValueError, IndexError):
        return False


def _match_day_of_week(condition_value: str) -> bool:
    """Return True if today (UTC) is in the comma-separated list of day names."""
    allowed = {d.strip().lower() for d in condition_value.split(",")}
    today = _DAY_NAMES[datetime.now(timezone.utc).weekday()]
    return today in allowed


def _evaluate_condition(cond: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate a single condition against the room context."""
    ctype = (cond.get("type") or "").lower()
    operator = (cond.get("operator") or "contains").lower()
    value = cond.get("value") or ""

    if ctype == "company_name":
        return _match_string(operator, context.get("company_name") or "", value)
    if ctype == "contact_name":
        return _match_string(operator, context.get("contact_name") or "", value)
    if ctype == "subject":
        return _match_string(operator, context.get("subject") or "", value)
    if ctype == "time_between":
        return _match_time_between(value)
    if ctype == "day_of_week":
        return _match_day_of_week(value)
    # Unknown condition type — does not match
    return False


def _evaluate_rule(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    """Return True if all conditions in the rule match the context."""
    conditions = rule.get("conditions") or []
    if not conditions:
        # A rule with no conditions only applies when it is the default fallback.
        return bool(rule.get("is_default"))
    return all(_evaluate_condition(c, context) for c in conditions)


async def apply_auto_assign(
    room_id: int,
    *,
    company_name: str | None = None,
    contact_name: str | None = None,
    subject: str | None = None,
) -> int | None:
    """Evaluate all active rules and assign the room if a rule matches.

    Returns the assigned technician's user_id, or ``None`` if no rule matched
    or the matched tech was not found.
    """
    from app.repositories import chat as chat_repo

    context: dict[str, Any] = {
        "company_name": company_name or "",
        "contact_name": contact_name or "",
        "subject": subject or "",
    }

    try:
        rules = await repo.list_rules(active_only=True)
    except Exception as exc:
        log_error("chat_auto_assign: failed to load rules", error=str(exc))
        return None

    default_rule: dict[str, Any] | None = None
    matched_rule: dict[str, Any] | None = None

    for rule in rules:
        if rule.get("is_default"):
            default_rule = rule
            continue
        if _evaluate_rule(rule, context):
            matched_rule = rule
            break

    resolved_rule = matched_rule or default_rule
    if not resolved_rule:
        return None

    tech_user_id: int | None = resolved_rule.get("assigned_tech_user_id")
    if not tech_user_id:
        return None

    try:
        await chat_repo.assign_tech(room_id, tech_user_id)
        log_info(
            "chat_auto_assign: assigned room",
            room_id=room_id,
            rule_id=resolved_rule.get("id"),
            rule_name=resolved_rule.get("name"),
            tech_user_id=tech_user_id,
        )
    except Exception as exc:
        log_error("chat_auto_assign: failed to assign tech", room_id=room_id, error=str(exc))
        return None

    return tech_user_id
