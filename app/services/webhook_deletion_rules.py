from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter

from app.core.logging import log_info
from app.repositories import webhook_deletion_rules as rules_repo
from app.repositories import webhook_events as events_repo
from app.services.cron_expression import for_croniter

OPERATORS = {"equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "is_empty", "is_not_empty", "greater_than", "less_than"}


def field_value(event: dict[str, Any], path: str) -> Any:
    value: Any = event
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def condition_matches(event: dict[str, Any], condition: dict[str, Any]) -> bool:
    actual = field_value(event, str(condition.get("field", "")))
    operator = str(condition.get("operator", "equals"))
    expected = condition.get("value")
    if operator == "is_empty": return actual in (None, "", [], {})
    if operator == "is_not_empty": return actual not in (None, "", [], {})
    if operator in {"greater_than", "less_than"}:
        try:
            left, right = float(actual), float(expected)
            return left > right if operator == "greater_than" else left < right
        except (TypeError, ValueError):
            return False
    left, right = str(actual or "").casefold(), str(expected or "").casefold()
    return {"equals": left == right, "not_equals": left != right,
            "contains": right in left, "not_contains": right not in left,
            "starts_with": left.startswith(right), "ends_with": left.endswith(right)}.get(operator, False)


def rule_matches(event: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    return bool(conditions) and all(condition_matches(event, item) for item in conditions)


def next_run(cron_expression: str, reference: datetime | None = None) -> datetime:
    reference = reference or datetime.now(timezone.utc)
    return croniter(for_croniter(cron_expression), reference).get_next(datetime)


async def apply_event_rules(event: dict[str, Any]) -> bool:
    for rule in await rules_repo.list_rules(enabled_only=True):
        if rule["execution_type"] == "event" and rule_matches(event, rule["conditions"]):
            await events_repo.delete_event(int(event["id"]))
            log_info("Webhook event deleted by rule", event_id=event["id"], rule_id=rule["id"])
            return True
    return False


async def run_scheduled_rules(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    deleted = 0
    events = await events_repo.list_events(limit=5000)
    for rule in await rules_repo.list_rules(enabled_only=True):
        if rule["execution_type"] != "scheduled": continue
        due = rule.get("next_run_at")
        if due and due.tzinfo is None: due = due.replace(tzinfo=timezone.utc)
        if due and due > now: continue
        for event in list(events):
            if rule_matches(event, rule["conditions"]):
                await events_repo.delete_event(int(event["id"]))
                events.remove(event)
                deleted += 1
        await rules_repo.mark_run(int(rule["id"]), next_run_at=next_run(rule["cron_expression"], now))
    retention = await rules_repo.get_retention()
    if bool(retention.get("enabled")):
        value = int(retention["retention_value"])
        unit = str(retention["retention_unit"])
        duration = {
            "immediately": timedelta(0),
            "minutes": timedelta(minutes=value),
            "hours": timedelta(hours=value),
            "days": timedelta(days=value),
        }[unit]
        deleted += await events_repo.delete_before(now - duration)
    return deleted
