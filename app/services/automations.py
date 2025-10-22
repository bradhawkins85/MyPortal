from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from croniter import croniter

from loguru import logger

from app.core.database import db
from app.repositories import automations as automation_repo
from app.services import modules as modules_service
from app.services import system_variables


TRIGGER_EVENTS: list[dict[str, str]] = [
    {"value": "tickets.created", "label": "Ticket created"},
    {"value": "tickets.updated", "label": "Ticket updated"},
    {"value": "tickets.closed", "label": "Ticket closed"},
    {"value": "tickets.assigned", "label": "Ticket assigned"},
    {"value": "webhook.delivered", "label": "Webhook delivered"},
]


def list_trigger_events() -> list[dict[str, str]]:
    """Return the available automation trigger event options."""

    return [dict(option) for option in TRIGGER_EVENTS]


_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

_TOKEN_PATTERN = re.compile(r"\{\{\s*([^\s{}]+)\s*\}\}")


def _build_constant_token_map(context: Mapping[str, Any] | None) -> dict[str, Any]:
    ticket: Mapping[str, Any] | None = None
    if isinstance(context, Mapping):
        possible_ticket = context.get("ticket")
        if isinstance(possible_ticket, Mapping):
            ticket = possible_ticket

    tokens: dict[str, Any] = dict(system_variables.get_system_variables(ticket=ticket))
    if context:
        tokens.update(system_variables.build_context_variables(context))
    return tokens


def _coerce_template_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, (int, float, bool, str)):
        return value
    return value


def _stringify_template_value(value: Any) -> str:
    coerced = _coerce_template_value(value)
    if isinstance(coerced, str):
        return coerced
    if isinstance(coerced, (int, float, bool)):
        return str(coerced)
    if coerced is None or coerced == "":
        return ""
    if isinstance(coerced, Mapping):
        try:
            return json.dumps(coerced)
        except TypeError:
            return str(coerced)
    if isinstance(coerced, Sequence) and not isinstance(coerced, (str, bytes, bytearray)):
        try:
            return json.dumps(coerced)
        except TypeError:
            return str(coerced)
    return str(coerced)


def _interpolate_string(
    value: str,
    context: Mapping[str, Any] | None,
    *,
    legacy_tokens: Mapping[str, Any] | None = None,
) -> Any:
    if not context and not legacy_tokens:
        return value
    token_map = legacy_tokens or {}
    stripped = value.strip()
    single_match = _TOKEN_PATTERN.fullmatch(stripped)
    if single_match:
        token_name = single_match.group(1)
        resolved = _resolve_context_value(context, token_name)
        if resolved is None and token_name in token_map:
            resolved = token_map[token_name]
        return _coerce_template_value(resolved)

    def _replace(match: re.Match[str]) -> str:
        token_name = match.group(1)
        resolved = _resolve_context_value(context, token_name)
        if resolved is None and token_name in token_map:
            resolved = token_map[token_name]
        return _stringify_template_value(resolved)

    return _TOKEN_PATTERN.sub(_replace, value)


def _interpolate_payload(
    value: Any,
    context: Mapping[str, Any] | None,
    *,
    legacy_tokens: Mapping[str, Any] | None = None,
) -> Any:
    if legacy_tokens is None:
        legacy_tokens = _build_constant_token_map(context)
    if isinstance(value, str):
        return _interpolate_string(value, context, legacy_tokens=legacy_tokens)
    if isinstance(value, Mapping):
        return {
            key: _interpolate_payload(item, context, legacy_tokens=legacy_tokens)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _interpolate_payload(item, context, legacy_tokens=legacy_tokens)
            for item in value
        ]
    return value


def _normalise_actions(actions: Any) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    if not isinstance(actions, list):
        return normalised
    for entry in actions:
        if not isinstance(entry, Mapping):
            continue
        module = str(entry.get("module") or "").strip()
        if not module:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        normalised.append({"module": module, "payload": dict(payload)})
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


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        return any(_value_matches(actual, candidate) for candidate in expected)
    return actual == expected


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
        if isinstance(expected, Mapping):
            if not isinstance(actual, Mapping):
                return False
            if not _filters_match(expected, actual):
                return False
        else:
            if not _value_matches(actual, expected):
                return False
    return True


def calculate_next_run(
    automation: Mapping[str, Any],
    *,
    reference: datetime | None = None,
) -> datetime | None:
    reference_time = reference or datetime.now(timezone.utc)
    kind = str(automation.get("kind") or "").strip().lower()
    if kind != "scheduled":
        return None
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


async def _execute_automation(
    automation: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    automation_id = int(automation.get("id"))
    await automation_repo.mark_started(automation_id)
    started_at = datetime.now(timezone.utc)
    status = "succeeded"
    result_payload: Any = None
    error_message: str | None = None
    payload = automation.get("action_payload")
    actions = _normalise_actions(payload.get("actions")) if isinstance(payload, Mapping) else []
    try:
        if actions:
            results: list[dict[str, Any]] = []
            for action in actions:
                module_slug = action["module"]
                payload_source = action.get("payload")
                module_payload = (
                    dict(payload_source)
                    if isinstance(payload_source, Mapping)
                    else {}
                )
                module_payload = _interpolate_payload(module_payload, context)
                if context:
                    module_payload.setdefault("context", context)
                try:
                    action_result = await modules_service.trigger_module(
                        module_slug,
                        module_payload,
                        background=False,
                    )
                    results.append(
                        {"module": module_slug, "status": "succeeded", "result": action_result}
                    )
                except Exception as exc:  # pragma: no cover - network/runtime guard
                    status = "failed"
                    error_message = str(exc)
                    results.append(
                        {"module": module_slug, "status": "failed", "error": str(exc)}
                    )
                    logger.error(
                        "Automation execution failed",
                        automation_id=automation_id,
                        module=module_slug,
                        error=str(exc),
                    )
                    break
            if status == "failed" and not error_message:
                error_message = "One or more trigger actions failed"
            result_payload = results
        else:
            if not isinstance(payload, Mapping):
                payload = {}
            module_slug = automation.get("action_module")
            if module_slug:
                module_payload = _interpolate_payload(dict(payload), context)
                if context:
                    module_payload.setdefault("context", context)
                result_payload = await modules_service.trigger_module(
                    str(module_slug), module_payload, background=False
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

    event_key = str(event_name or "").strip()
    if not event_key:
        return []

    try:
        automations = await automation_repo.list_event_automations(event_key)
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
        _schedule_background_execution(
            _execute_automation(automation, context=context),
            automation_id=automation_id,
        )
        matched.append({
            "automation_id": automation_id,
            "status": "queued",
        })
    return matched

