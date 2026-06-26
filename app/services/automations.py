from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
import re
from functools import lru_cache
from time import monotonic
from typing import Any

from croniter import croniter

from loguru import logger

from app.core.database import db
from app.repositories import automations as automation_repo
from app.repositories import tickets as tickets_repo
from app.services import modules as modules_service
from app.services import value_templates


TRIGGER_EVENTS: list[dict[str, str]] = [
    {"value": "tickets.created", "label": "Ticket created"},
    {"value": "tickets.updated", "label": "Ticket updated"},
    {"value": "tickets.replied", "label": "Ticket reply added"},
    {"value": "tickets.details_updated", "label": "Ticket Details Updated"},
    {"value": "tickets.closed", "label": "Ticket closed"},
    {"value": "tickets.assigned", "label": "Ticket assigned"},
    {"value": "webhook.delivered", "label": "Webhook delivered"},
]

_EVENT_ALIASES: dict[str, tuple[str, ...]] = {
    # Backward compatibility for legacy singular ticket event names.
    "tickets.created": ("ticket.created",),
    "tickets.updated": ("ticket.updated",),
    "tickets.replied": ("ticket.replied",),
    "tickets.closed": ("ticket.closed",),
    "tickets.assigned": ("ticket.assigned",),
}


def list_trigger_events() -> list[dict[str, str]]:
    """Return the available automation trigger event options."""

    return [dict(option) for option in TRIGGER_EVENTS]


_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_RECENT_REPLY_EVENT_SECONDS = 10.0
_RECENT_REPLY_EVENT_EXECUTIONS: dict[tuple[int, int, int], float] = {}


def _reply_event_dedupe_key(
    automation_id: int,
    event_key: str,
    context: Mapping[str, Any] | None,
) -> tuple[int, int, int] | None:
    """Return a short-lived idempotency key for reply-backed ticket events."""

    if event_key not in {"tickets.updated", "ticket.updated", "tickets.replied", "ticket.replied"}:
        return None
    if not isinstance(context, Mapping):
        return None
    ticket = context.get("ticket")
    if not isinstance(ticket, Mapping):
        return None
    latest_reply = ticket.get("latest_reply")
    if not isinstance(latest_reply, Mapping):
        return None
    try:
        ticket_id = int(ticket.get("id"))
        reply_id = int(latest_reply.get("id"))
    except (TypeError, ValueError):
        return None
    if ticket_id <= 0 or reply_id <= 0:
        return None
    return (automation_id, ticket_id, reply_id)


def _claim_reply_event_execution(
    automation_id: int,
    event_key: str,
    context: Mapping[str, Any] | None,
) -> bool:
    """Prevent duplicate automation runs caused by paired reply/update events."""

    dedupe_key = _reply_event_dedupe_key(automation_id, event_key, context)
    if dedupe_key is None:
        return True

    now = monotonic()
    expired_before = now - _RECENT_REPLY_EVENT_SECONDS
    for key, seen_at in list(_RECENT_REPLY_EVENT_EXECUTIONS.items()):
        if seen_at < expired_before:
            _RECENT_REPLY_EVENT_EXECUTIONS.pop(key, None)

    previous = _RECENT_REPLY_EVENT_EXECUTIONS.get(dedupe_key)
    if previous is not None and previous >= expired_before:
        return False

    _RECENT_REPLY_EVENT_EXECUTIONS[dedupe_key] = now
    return True



def _context_ticket_identity(context: Mapping[str, Any] | None, result: Any = None) -> tuple[int | None, str | None]:
    ticket_id: int | None = None
    ticket_number: str | None = None
    if isinstance(result, Mapping):
        raw_ticket_id = result.get("ticket_id")
        if raw_ticket_id is not None:
            try:
                ticket_id = int(raw_ticket_id)
            except (TypeError, ValueError):
                ticket_id = None
        raw_number = result.get("ticket_number") or result.get("number")
        if raw_number is not None:
            ticket_number = str(raw_number)
    if isinstance(context, Mapping):
        ticket = context.get("ticket")
        if isinstance(ticket, Mapping):
            if ticket_id is None and ticket.get("id") is not None:
                try:
                    ticket_id = int(ticket.get("id"))
                except (TypeError, ValueError):
                    ticket_id = None
            if ticket_number is None:
                raw_number = ticket.get("ticket_number") or ticket.get("number")
                if raw_number is not None:
                    ticket_number = str(raw_number)
    return ticket_id, ticket_number


def _previous_values_from_result(result: Any) -> Any:
    if not isinstance(result, Mapping):
        return None
    previous = result.get("previous_values")
    if previous is not None:
        return previous
    nested = result.get("result")
    if isinstance(nested, Mapping):
        return nested.get("previous_values")
    return None


async def _record_action_history(
    automation: Mapping[str, Any],
    *,
    action_name: str,
    action_module: str | None,
    status: str,
    result: Any,
    error_message: str | None,
    context: Mapping[str, Any] | None,
) -> None:
    automation_id = int(automation.get("id"))
    ticket_id, ticket_number = _context_ticket_identity(context, result)
    try:
        await automation_repo.record_history(
            automation_id=automation_id,
            action_name=action_name,
            action_module=action_module,
            ticket_id=ticket_id,
            ticket_number=ticket_number,
            status=status,
            previous_values=_previous_values_from_result(result),
            result_payload=result,
            error_message=error_message,
        )
    except Exception as exc:  # pragma: no cover - history must not break automations
        logger.warning("Failed to record automation history", automation_id=automation_id, error=str(exc))

def _normalise_actions(actions: Any) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    if not isinstance(actions, list):
        return normalised
    for index, entry in enumerate(actions):
        if not isinstance(entry, Mapping):
            continue
        module = str(entry.get("module") or "").strip()
        if not module:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        order_raw = entry.get("order")
        try:
            order = int(order_raw) if order_raw is not None else index
        except (TypeError, ValueError):
            order = index
        note = str(entry.get("note") or "").strip()
        action: dict[str, Any] = {"order": order, "module": module, "payload": dict(payload)}
        if note:
            action["note"] = note
        normalised.append(action)
    normalised.sort(key=lambda a: a["order"])
    return normalised


def _resolve_context_value(context: Mapping[str, Any] | None, path: str) -> Any:
    if not context or not path:
        return None
    current: Any = context
    for segment in path.split('.'):
        if isinstance(current, Mapping):
            current = current.get(segment)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                index = int(segment)
            except (TypeError, ValueError):
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


@lru_cache(maxsize=256)
def _compile_like_pattern(pattern: str) -> re.Pattern[str]:
    pieces: list[str] = ["^"]
    escaped = False
    for char in pattern:
        if escaped:
            pieces.append(re.escape(char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "%":
            pieces.append(".*")
            continue
        if char == "_":
            pieces.append(".")
            continue
        pieces.append(re.escape(char))
    if escaped:
        pieces.append(re.escape("\\"))
    pieces.append("$")
    return re.compile("".join(pieces))


def _string_value_matches(actual: Any, expected: str) -> bool:
    if not isinstance(actual, str):
        return False
    pattern = _compile_like_pattern(expected)
    return bool(pattern.fullmatch(actual))

_REGEX_PATTERN_MAX_LENGTH = 256
_REGEX_INPUT_MAX_LENGTH = 4096


def _split_membership_values(expected: Any) -> list[Any]:
    if isinstance(expected, str):
        return [item.strip() for item in expected.split(",") if item.strip()]
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        return list(expected)
    return [expected]


def _string_operator_matches(actual: Any, expected: Any, operator: str) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    if operator == "starts_with":
        return actual.startswith(expected)
    if operator == "ends_with":
        return actual.endswith(expected)
    if operator == "contains":
        return expected in actual
    if operator == "not_contains":
        return expected not in actual
    if operator == "regex":
        if len(expected) > _REGEX_PATTERN_MAX_LENGTH:
            return False
        try:
            return bool(re.search(expected, actual[:_REGEX_INPUT_MAX_LENGTH]))
        except re.error:
            return False
    return False


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        return any(_value_matches(actual, candidate) for candidate in expected)
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)):
        return any(_value_matches(candidate, expected) for candidate in actual)
    if isinstance(expected, str):
        return _string_value_matches(actual, expected)
    return actual == expected


def _coerce_comparable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return text
        try:
            return float(text)
        except ValueError:
            return text.casefold()
    return value


def _compare_values(actual: Any, expected: Any, operator: str) -> bool:
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)):
        if operator in {"not_contains", "not_in"}:
            return all(_compare_values(candidate, expected, operator) for candidate in actual)
        return any(_compare_values(candidate, expected, operator) for candidate in actual)

    if operator in {"in", "not_in"}:
        candidates = _split_membership_values(expected)
        matched = any(_value_matches(actual, candidate) for candidate in candidates)
        return matched if operator == "in" else not matched

    string_operators = {"starts_with", "ends_with", "contains", "not_contains", "regex"}
    if operator in string_operators:
        return _string_operator_matches(actual, expected, operator)

    actual_value = _coerce_comparable(actual)
    expected_value = _coerce_comparable(expected)
    try:
        if operator == "not_equals":
            return not _value_matches(actual, expected)
        if operator == "greater_than":
            return actual_value > expected_value
        if operator == "greater_than_or_equal":
            return actual_value >= expected_value
        if operator == "less_than":
            return actual_value < expected_value
        if operator == "less_than_or_equal":
            return actual_value <= expected_value
    except TypeError:
        return False
    return _value_matches(actual, expected)


def _operator_filters_match(
    filters: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    *,
    operator: str,
) -> bool:
    if not filters:
        return False
    for key, expected in filters.items():
        lookup_key = str(key)
        actual = _resolve_context_value(context, lookup_key)
        if actual is None and "." not in lookup_key and isinstance(context, Mapping):
            ticket_ctx = context.get("ticket")
            if isinstance(ticket_ctx, Mapping):
                fallback = _resolve_context_value(ticket_ctx, lookup_key)
                if fallback is not None:
                    actual = fallback
        if not _compare_values(actual, expected, operator):
            return False
    return True


def _filters_match(filters: Mapping[str, Any] | None, context: Mapping[str, Any] | None) -> bool:
    if not filters:
        return True
    if not isinstance(filters, Mapping):
        return False

    if "any" in filters:
        options = filters["any"]
        if not isinstance(options, Sequence):
            return False
        return any(
            _filters_match(option if isinstance(option, Mapping) else {"match": option}, context)
            for option in options
        )

    if "all" in filters:
        requirements = filters["all"]
        if not isinstance(requirements, Sequence):
            return False
        return all(
            _filters_match(requirement if isinstance(requirement, Mapping) else {"match": requirement}, context)
            for requirement in requirements
        )

    if "not" in filters:
        return not _filters_match(filters.get("not"), context)

    operator_keys = {
        "not_equals": "not_equals",
        "in": "in",
        "not_in": "not_in",
        "gt": "greater_than",
        "greater_than": "greater_than",
        "gte": "greater_than_or_equal",
        "greater_than_or_equal": "greater_than_or_equal",
        "lt": "less_than",
        "less_than": "less_than",
        "lte": "less_than_or_equal",
        "less_than_or_equal": "less_than_or_equal",
        "starts_with": "starts_with",
        "ends_with": "ends_with",
        "contains": "contains",
        "not_contains": "not_contains",
        "regex": "regex",
    }
    for filter_key, operator in operator_keys.items():
        comparison_filters = filters.get(filter_key)
        if isinstance(comparison_filters, Mapping):
            return _operator_filters_match(comparison_filters, context, operator=operator)

    matchers: Mapping[str, Any] | None
    if "match" in filters and isinstance(filters["match"], Mapping):
        matchers = filters["match"]
    else:
        matchers = filters

    if not isinstance(matchers, Mapping):
        return False

    for key, expected in matchers.items():
        lookup_key = str(key)
        actual = _resolve_context_value(context, lookup_key)
        # Backward-compatible fallback: bare keys (no dot) that don't resolve at
        # the top level are re-tried under "ticket" when that sub-context exists.
        # This makes filters like {"status": "new"} equivalent to
        # {"ticket.status": "new"} for ticket events.
        if actual is None and "." not in lookup_key and isinstance(context, Mapping):
            ticket_ctx = context.get("ticket")
            if isinstance(ticket_ctx, Mapping):
                fallback = _resolve_context_value(ticket_ctx, lookup_key)
                if fallback is not None:
                    actual = fallback
        if isinstance(expected, Mapping):
            if not isinstance(actual, Mapping):
                return False
            if not _filters_match(expected, actual):
                return False
        else:
            if not _value_matches(actual, expected):
                return False
    return True


def _serialise_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return None


def _age_metrics(value: Any, *, now: datetime) -> dict[str, Any]:
    if not isinstance(value, datetime):
        return {"seconds": None, "minutes": None, "hours": None, "days": None}
    timestamp = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (now - timestamp).total_seconds())
    return {
        "seconds": seconds,
        "minutes": seconds / 60,
        "hours": seconds / 3600,
        "days": seconds / 86400,
    }


def _attach_ticket_age_context(ticket: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    enriched = dict(ticket)
    created_at = enriched.get("created_at")
    updated_at = enriched.get("updated_at")
    latest_reply_at = enriched.get("latest_reply_at")
    last_activity_at = latest_reply_at or updated_at or created_at

    enriched["age"] = _age_metrics(created_at, now=now)
    enriched["updated_age"] = _age_metrics(updated_at, now=now)
    enriched["last_reply_at"] = latest_reply_at
    enriched["last_activity_at"] = last_activity_at
    enriched["last_reply_age"] = _age_metrics(latest_reply_at or created_at, now=now)
    enriched["last_activity_age"] = _age_metrics(last_activity_at, now=now)

    # Flat aliases keep the builder simple and make advanced JSON easy to read.
    for prefix in ("age", "updated_age", "last_reply_age", "last_activity_age"):
        metrics = enriched.get(prefix)
        if isinstance(metrics, Mapping):
            for unit, metric_value in metrics.items():
                enriched[f"{prefix}_{unit}"] = metric_value
    enriched["last_reply_at_iso"] = _serialise_datetime(latest_reply_at)
    enriched["last_activity_at_iso"] = _serialise_datetime(last_activity_at)
    return enriched


def _is_ticket_scoped_scheduled_automation(automation: Mapping[str, Any]) -> bool:
    if str(automation.get("kind") or "").strip().lower() != "scheduled":
        return False
    filters = automation.get("trigger_filters")
    if not isinstance(filters, Mapping):
        return False
    haystack = json.dumps({"filters": filters}, default=str)
    return "ticket" in haystack


def calculate_next_run(
    automation: Mapping[str, Any],
    *,
    reference: datetime | None = None,
) -> datetime | None:
    reference_time = reference or datetime.now(timezone.utc)
    kind = str(automation.get("kind") or "").strip().lower()
    if kind != "scheduled":
        return None
    
    # Handle one-time scheduling
    run_once = automation.get("run_once")
    if run_once:
        scheduled_time = automation.get("scheduled_time")
        last_run_at = automation.get("last_run_at")
        
        # If already run, don't schedule again
        if last_run_at is not None:
            return None
        
        # Return the scheduled time if set (even if in the past, to run ASAP)
        if scheduled_time:
            if isinstance(scheduled_time, datetime):
                scheduled_utc = scheduled_time.astimezone(timezone.utc) if scheduled_time.tzinfo else scheduled_time.replace(tzinfo=timezone.utc)
                return scheduled_utc
        return None
    
    # Handle recurring scheduling
    cron_expression = str(automation.get("cron_expression") or "").strip()
    if cron_expression:
        try:
            iterator = croniter(cron_expression, reference_time)
            next_time = iterator.get_next(datetime)
            return next_time.astimezone(timezone.utc)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "Invalid cron expression on automation",
                automation_id=automation.get("id"),
                cron=cron_expression,
                error=str(exc),
            )
    cadence = str(automation.get("cadence") or "").strip().lower()
    if cadence == "hourly":
        return reference_time + timedelta(hours=1)
    if cadence == "daily":
        return reference_time + timedelta(days=1)
    if cadence == "weekly":
        return reference_time + timedelta(weeks=1)
    if cadence == "monthly":
        return reference_time + timedelta(days=30)
    return None


async def refresh_schedule(automation_id: int) -> dict[str, Any] | None:
    if not db.is_connected():
        logger.info(
            "Skipping automation schedule refresh because the database is not connected",
            automation_id=automation_id,
        )
        return None

    try:
        automation = await automation_repo.get_automation(automation_id)
    except RuntimeError as exc:
        logger.warning(
            "Failed to load automation for schedule refresh", automation_id=automation_id, error=str(exc)
        )
        return None
    if not automation:
        return None
    next_run = calculate_next_run(automation)
    await automation_repo.set_next_run(automation_id, next_run)
    automation["next_run_at"] = next_run
    return automation


async def refresh_all_schedules() -> None:
    if not db.is_connected():
        logger.info("Skipping automation schedule refresh because the database is not connected")
        return

    try:
        automations = await automation_repo.list_automations(status="active", limit=1000)
    except RuntimeError as exc:
        logger.warning(
            "Failed to load automations for schedule refresh", error=str(exc)
        )
        return
    now = datetime.now(timezone.utc)
    for automation in automations:
        next_run = calculate_next_run(automation, reference=now)
        await automation_repo.set_next_run(int(automation["id"]), next_run)


async def _invoke_automation_actions_for_context(
    automation: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None,
) -> tuple[Any, str | None]:
    """Invoke configured module action(s) without recording an automation run."""

    payload = automation.get("action_payload")
    actions = _normalise_actions(payload.get("actions")) if isinstance(payload, Mapping) else []
    if actions:
        results: list[dict[str, Any]] = []
        first_error: str | None = None
        for action in actions:
            module_slug = action["module"]
            payload_source = action.get("payload")
            module_payload = dict(payload_source) if isinstance(payload_source, Mapping) else {}
            module_payload = await value_templates.render_value_async(module_payload, context)
            if context:
                module_payload.setdefault("context", context)
            try:
                action_result = await modules_service.trigger_module(
                    module_slug,
                    module_payload,
                    background=False,
                )
            except Exception as exc:  # pragma: no cover - network/runtime guard
                exc_message = str(exc)
                if not first_error:
                    first_error = exc_message
                failure_entry = {"module": module_slug, "status": "failed", "error": exc_message}
                results.append(failure_entry)
                await _record_action_history(
                    automation,
                    action_name=action.get("note") or module_slug,
                    action_module=module_slug,
                    status="failed",
                    result=failure_entry,
                    error_message=exc_message,
                    context=context,
                )
                continue

            action_status_raw = action_result.get("status") if isinstance(action_result, Mapping) else None
            action_error = None
            action_reason = None
            if isinstance(action_result, Mapping):
                action_status_raw = action_result.get("status") or action_result.get("event_status")
                action_error = action_result.get("error") or action_result.get("last_error") or None
                action_reason = action_result.get("reason")
            action_status = str(action_status_raw or "").strip().lower() or "unknown"
            result_entry: dict[str, Any] = {
                "module": module_slug,
                "status": action_status,
                "result": action_result,
            }
            if action_error:
                result_entry["error"] = action_error
            if action_reason:
                result_entry["reason"] = action_reason
            results.append(result_entry)
            history_status = "failed" if action_status in {"failed", "error"} or (action_status == "unknown" and action_error) else "succeeded"
            await _record_action_history(
                automation,
                action_name=action.get("note") or module_slug,
                action_module=module_slug,
                status=history_status,
                result=result_entry,
                error_message=str(action_error) if action_error else None,
                context=context,
            )
            if action_status in {"failed", "error"} or (action_status == "unknown" and action_error):
                if not first_error:
                    first_error = str(action_error or "Module action failed")
        return results, first_error

    if not isinstance(payload, Mapping):
        payload = {}
    module_slug = automation.get("action_module")
    if module_slug:
        module_payload = await value_templates.render_value_async(dict(payload), context)
        if context:
            module_payload.setdefault("context", context)
        result = await modules_service.trigger_module(str(module_slug), module_payload, background=False)
        history_error = None
        history_status = "succeeded"
        if isinstance(result, Mapping):
            result_status = str(result.get("status") or result.get("event_status") or "").strip().lower()
            result_error = result.get("error") or result.get("last_error") or None
            if result_status in {"failed", "error"} or result_error:
                history_status = "failed"
                history_error = str(result_error or "Module action failed")
                await _record_action_history(
                    automation,
                    action_name=str(module_slug),
                    action_module=str(module_slug),
                    status=history_status,
                    result=result,
                    error_message=history_error,
                    context=context,
                )
                return result, history_error
        await _record_action_history(
            automation,
            action_name=str(module_slug),
            action_module=str(module_slug),
            status=history_status,
            result=result,
            error_message=history_error,
            context=context,
        )
        return result, None
    return {"status": "skipped", "reason": "No action module configured"}, None


async def preview_scheduled_ticket_automation(
    automation: Mapping[str, Any],
    *,
    limit: int = 1000,
) -> dict[str, Any]:
    """Return tickets that would be actioned by a scheduled ticket automation.

    This mirrors the scheduled ticket scan path but never invokes automation
    actions, so admins can safely inspect the next run impact before enabling
    or manually executing an automation.
    """

    if str(automation.get("kind") or "").strip().lower() != "scheduled":
        raise ValueError("Only scheduled automations can be previewed")

    now = datetime.now(timezone.utc)
    raw_filters = automation.get("trigger_filters")
    filters = raw_filters if isinstance(raw_filters, Mapping) else None
    scan_limit = max(1, min(int(limit or 1000), 5000))
    scanned = await tickets_repo.list_tickets_for_automation_scan(limit=scan_limit)
    matches: list[dict[str, Any]] = []

    from app.services import tickets as tickets_service

    for ticket in scanned:
        ticket_context = _attach_ticket_age_context(ticket, now=now)
        try:
            enriched_ticket = await tickets_service._enrich_ticket_context(ticket_context)
        except Exception:  # pragma: no cover - defensive fallback
            enriched_ticket = ticket_context
        context = {
            "ticket": _attach_ticket_age_context(enriched_ticket, now=now),
            "ticket_update": {
                "actor_type": "automation",
                "actor_label": "Automation",
                "actor_user": None,
            },
            "schedule": {
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "checked_at": now.isoformat(),
                "preview": True,
            },
        }
        if not _filters_match(filters, context):
            continue
        match = dict(enriched_ticket)
        match["last_reply_at"] = ticket_context.get("last_reply_at")
        match["last_activity_at"] = ticket_context.get("last_activity_at")
        match["age_days"] = ticket_context.get("age_days")
        match["last_activity_age_days"] = ticket_context.get("last_activity_age_days")
        matches.append(match)

    return {
        "automation_id": automation.get("id"),
        "automation_name": automation.get("name"),
        "mode": "scheduled_ticket_preview",
        "checked_at": now,
        "scan_limit": scan_limit,
        "scanned": len(scanned),
        "matched": len(matches),
        "tickets": matches,
    }


async def preview_scheduled_ticket_automation_by_id(automation_id: int, *, limit: int = 1000) -> dict[str, Any]:
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        raise ValueError(f"Automation {automation_id} not found")
    return await preview_scheduled_ticket_automation(automation, limit=limit)


async def _execute_scheduled_ticket_automation(automation: Mapping[str, Any]) -> dict[str, Any]:
    """Run a scheduled automation once for each ticket matching its filters."""

    now = datetime.now(timezone.utc)
    raw_filters = automation.get("trigger_filters")
    filters = raw_filters if isinstance(raw_filters, Mapping) else None
    scanned = await tickets_repo.list_tickets_for_automation_scan(limit=1000)
    matched = 0
    succeeded = 0
    failed = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    # Local import avoids a circular import during application startup because
    # the ticket service emits automation events.
    from app.services import tickets as tickets_service

    for ticket in scanned:
        ticket_context = _attach_ticket_age_context(ticket, now=now)
        try:
            enriched_ticket = await tickets_service._enrich_ticket_context(ticket_context)
        except Exception:  # pragma: no cover - defensive fallback
            enriched_ticket = ticket_context
        context = {
            "ticket": _attach_ticket_age_context(enriched_ticket, now=now),
            "ticket_update": {
                "actor_type": "automation",
                "actor_label": "Automation",
                "actor_user": None,
            },
            "schedule": {
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "checked_at": now.isoformat(),
            },
        }
        if not _filters_match(filters, context):
            continue
        matched += 1
        action_result, action_error = await _invoke_automation_actions_for_context(
            automation,
            context=context,
        )
        ticket_result: dict[str, Any] = {
            "ticket_id": ticket.get("id"),
            "status": "failed" if action_error else "succeeded",
            "result": action_result,
        }
        if action_error:
            failed += 1
            ticket_result["error"] = action_error
        else:
            succeeded += 1
        results.append(ticket_result)

    if matched == 0:
        skipped = len(scanned)
    return {
        "mode": "scheduled_ticket_scan",
        "scanned": len(scanned),
        "matched": matched,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }


async def _execute_automation(
    automation: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    automation_id = int(automation.get("id"))
    
    # Use a distributed lock to ensure only one worker executes this automation
    lock_name = f"automation_exec_{automation_id}"
    
    async with db.acquire_lock(lock_name, timeout=1) as lock_acquired:
        if not lock_acquired:
            # Another worker is already executing this automation, skip silently
            logger.info(
                "Automation already running on another worker, skipping",
                automation_id=automation_id,
            )
            return {
                "status": "skipped",
                "reason": "Already running on another worker",
                "automation_id": automation_id,
            }
    
        await automation_repo.mark_started(automation_id)
        started_at = datetime.now(timezone.utc)
        status = "succeeded"
        result_payload: Any = None
        error_message: str | None = None
        payload = automation.get("action_payload")
        actions = _normalise_actions(payload.get("actions")) if isinstance(payload, Mapping) else []
        try:
            if context is None and _is_ticket_scoped_scheduled_automation(automation):
                result_payload = await _execute_scheduled_ticket_automation(automation)
                failed_count = int(result_payload.get("failed", 0)) if isinstance(result_payload, Mapping) else 0
                if failed_count > 0:
                    status = "failed"
                    error_message = f"{failed_count} scheduled ticket action(s) failed"
            elif actions:
                results: list[dict[str, Any]] = []
                for action in actions:
                    module_slug = action["module"]
                    payload_source = action.get("payload")
                    module_payload = (
                        dict(payload_source)
                        if isinstance(payload_source, Mapping)
                        else {}
                    )
                    module_payload = await value_templates.render_value_async(module_payload, context)
                    if context:
                        module_payload.setdefault("context", context)
                    try:
                        action_result = await modules_service.trigger_module(
                            module_slug,
                            module_payload,
                            background=False,
                        )
                    except Exception as exc:  # pragma: no cover - network/runtime guard
                        exc_message = str(exc)
                        status = "failed"
                        if not error_message:
                            error_message = exc_message
                        failure_entry = {"module": module_slug, "status": "failed", "error": exc_message}
                        results.append(failure_entry)
                        await _record_action_history(
                            automation,
                            action_name=action.get("note") or module_slug,
                            action_module=module_slug,
                            status="failed",
                            result=failure_entry,
                            error_message=exc_message,
                            context=context,
                        )
                        logger.error(
                            "Automation action failed",
                            automation_id=automation_id,
                            module=module_slug,
                            error=exc_message,
                        )
                        continue

                    if isinstance(action_result, Mapping):
                        action_status_raw = action_result.get("status") or action_result.get("event_status")
                        action_error = (
                            action_result.get("error")
                            or action_result.get("last_error")
                            or None
                        )
                        action_reason = action_result.get("reason")
                    else:
                        action_status_raw = None
                        action_error = None
                        action_reason = None

                    action_status = str(action_status_raw or "").strip().lower() or "unknown"

                    result_entry: dict[str, Any] = {
                        "module": module_slug,
                        "status": action_status,
                        "result": action_result,
                    }
                    if action_error:
                        result_entry["error"] = action_error
                    if action_reason:
                        result_entry["reason"] = action_reason
                    results.append(result_entry)
                    history_status = "failed" if action_status in {"failed", "error"} or (action_status == "unknown" and action_error) else "succeeded"
                    await _record_action_history(
                        automation,
                        action_name=action.get("note") or module_slug,
                        action_module=module_slug,
                        status=history_status,
                        result=result_entry,
                        error_message=str(action_error) if action_error else None,
                        context=context,
                    )

                    if action_status in {"failed", "error"} or (
                        action_status == "unknown" and action_error
                    ):
                        status = "failed"
                        if not error_message:
                            error_message = str(action_error or "Module action failed")
                if status == "failed" and not error_message:
                    error_message = "One or more trigger actions failed"
                result_payload = results
            else:
                if not isinstance(payload, Mapping):
                    payload = {}
                module_slug = automation.get("action_module")
                if module_slug:
                    module_payload = await value_templates.render_value_async(dict(payload), context)
                    if context:
                        module_payload.setdefault("context", context)
                    result_payload = await modules_service.trigger_module(
                        str(module_slug), module_payload, background=False
                    )
                    action_status = "succeeded"
                    action_error = None
                    if isinstance(result_payload, Mapping):
                        raw_status = str(result_payload.get("status") or result_payload.get("event_status") or "").strip().lower()
                        action_error = result_payload.get("error") or result_payload.get("last_error") or None
                        if raw_status in {"failed", "error"} or action_error:
                            action_status = "failed"
                            status = "failed"
                            error_message = str(action_error or "Module action failed")
                    await _record_action_history(
                        automation,
                        action_name=str(module_slug),
                        action_module=str(module_slug),
                        status=action_status,
                        result=result_payload,
                        error_message=str(action_error) if action_error else None,
                        context=context,
                    )
                else:
                    result_payload = {"status": "skipped", "reason": "No action module configured"}
        except Exception as exc:  # pragma: no cover - network/runtime guard
            status = "failed"
            error_message = str(exc)
            logger.error(
                "Automation execution failed",
                automation_id=automation_id,
                error=str(exc),
            )
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        await automation_repo.record_run(
            automation_id=automation_id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            result_payload=result_payload,
            error_message=error_message,
        )
        if status == "failed":
            await automation_repo.set_last_error(automation_id, error_message)
        else:
            await automation_repo.set_last_error(automation_id, None)
        next_reference = finished_at if status == "succeeded" else datetime.now(timezone.utc)
        next_run = calculate_next_run(automation, reference=next_reference)
        await automation_repo.set_next_run(automation_id, next_run)
        return {
            "status": status,
            "result": result_payload,
            "error": error_message,
            "started_at": started_at,
            "finished_at": finished_at,
            "next_run_at": next_run,
        }


async def process_due_automations(limit: int = 20) -> None:
    due = await automation_repo.list_due_automations(limit=limit)
    for automation in due:
        await _execute_automation(automation)


async def execute_now(automation_id: int) -> dict[str, Any]:
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        raise ValueError(f"Automation {automation_id} not found")
    return await _execute_automation(automation)


def _schedule_background_execution(
    coro: Awaitable[dict[str, Any]],
    *,
    automation_id: int,
) -> asyncio.Task[dict[str, Any]]:
    """Schedule an automation execution coroutine in the background."""

    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)

    def _cleanup(completed: asyncio.Task[dict[str, Any]]) -> None:
        _BACKGROUND_TASKS.discard(completed)
        try:
            completed.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(
                "Automation background task failed",
                automation_id=automation_id,
                error=str(exc),
            )

    task.add_done_callback(_cleanup)
    return task


async def handle_event(
    event_name: str,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Trigger event-based automations for the supplied event."""

    event_key = str(event_name or "").strip().lower()
    if not event_key:
        return []

    event_keys: list[str] = [event_key]
    for alias in _EVENT_ALIASES.get(event_key, ()):
        alias_key = str(alias or "").strip().lower()
        if alias_key and alias_key not in event_keys:
            event_keys.append(alias_key)

    automations: list[Mapping[str, Any]] = []
    seen_ids: set[int] = set()
    try:
        for key in event_keys:
            records = await automation_repo.list_event_automations(key)
            for record in records:
                try:
                    automation_id = int(record.get("id"))
                except (TypeError, ValueError):
                    continue
                if automation_id in seen_ids:
                    continue
                seen_ids.add(automation_id)
                automations.append(record)
    except RuntimeError as exc:
        logger.warning(
            "Failed to load event automations",
            event=event_key,
            error=str(exc),
        )
        return []

    matched: list[dict[str, Any]] = []
    for automation in automations:
        filters = automation.get("trigger_filters")
        filters_mapping = filters if isinstance(filters, Mapping) else None
        if not _filters_match(filters_mapping, context):
            continue
        automation_id = int(automation.get("id"))
        if not _claim_reply_event_execution(automation_id, event_key, context):
            matched.append({
                "automation_id": automation_id,
                "status": "skipped",
                "reason": "duplicate_reply_event",
            })
            continue
        _schedule_background_execution(
            _execute_automation(automation, context=context),
            automation_id=automation_id,
        )
        matched.append({
            "automation_id": automation_id,
            "status": "queued",
        })
    return matched
