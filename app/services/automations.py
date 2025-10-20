from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from croniter import croniter
from loguru import logger

from app.core.database import db
from app.repositories import automations as automation_repo
from app.services import modules as modules_service


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


async def _execute_automation(automation: Mapping[str, Any]) -> dict[str, Any]:
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
                module_payload = action.get("payload")
                try:
                    action_result = await modules_service.trigger_module(
                        module_slug, module_payload if isinstance(module_payload, Mapping) else {}
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
                result_payload = await modules_service.trigger_module(str(module_slug), payload)
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

