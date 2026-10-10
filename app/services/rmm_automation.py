"""Scheduled and onboarding RMM scripts.

Both build on the manual flow in :mod:`app.services.rmm_scripts`: the values a
technician enters are saved (encrypted) with the schedule or onboarding step,
and are resolved per device each time a run is queued.

* **Schedules** run a script on a cron schedule, separate from MyPortal's own
  scheduled tasks. :func:`automation_loop` checks for due schedules every
  :data:`TICK_SECONDS` on one instance at a time. A device that still has an
  unfinished run from the same schedule is skipped rather than given a second.
* **Onboarding** runs a company's ordered list of scripts the first time one of
  its devices' RMM agent enrols. Each step has a tag filter (for example Server
  or Workstation) and is skipped on devices without any of its tags. Steps run
  one at a time; the next is queued only once the previous one has finished. A
  failed step stops the sequence unless that step is set to continue on
  failure. :func:`advance_onboarding` moves a sequence on when a result arrives,
  and the loop re-checks running sequences so a run that expires or is
  cancelled still moves its sequence on.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter

from app.core.config import get_settings
from app.core.logging import log_info, log_warning
from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_automation as automation_repo
from app.repositories import tags as tags_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import cron_expression
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError
from app.services.singleton_jobs import singleton_run

TICK_SECONDS = 30
TARGET_MODES = ("all", "tags", "assets")
MAX_SCHEDULE_ASSETS = rmm_scripts.MAX_TARGETS_PER_RUN
MAX_SCHEDULE_TAGS = 50
# A scheduled run waits for an offline device until the schedule's next run,
# but never less than this, nor longer than a manual run would.
MIN_SCHEDULED_WAIT = timedelta(minutes=5)

_FINISH_WORDS = {
    "failed": "failed",
    "timed_out": "timed out",
    "cancelled": "was cancelled",
    "expired": "expired before the device collected it",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Saved values
# --------------------------------------------------------------------------- #


def decrypt_entries(blob: Any) -> dict[str, str]:
    if not blob:
        return {}
    try:
        values = json.loads(decrypt_secret(str(blob), allow_plaintext=False))
    except Exception:  # noqa: BLE001 - a corrupt value is treated as empty
        log_warning("Saved RMM script values could not be decrypted")
        return {}
    return {str(key): str(value) for key, value in values.items()} if isinstance(values, dict) else {}


def editable_values(script: Mapping[str, Any], entries: Mapping[str, str]) -> dict[str, str]:
    """Saved values for the editor. Typed secrets come back masked; saving the
    mask unchanged keeps the stored secret."""

    values: dict[str, str] = {}
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        if value and field_def.get("sensitive") and "{{" not in value:
            value = rmm_scripts.SENSITIVE_MASK
        if value:
            values[field_def["key"]] = value
    return values


def _merge_entries(
    script: Mapping[str, Any], submitted: Mapping[str, Any], previous: Mapping[str, str]
) -> dict[str, str]:
    merged = {str(key): "" if value is None else str(value) for key, value in submitted.items()}
    for field_def in rmm_scripts.script_fields(script):
        key = field_def["key"]
        if field_def.get("sensitive") and merged.get(key) == rmm_scripts.SENSITIVE_MASK:
            merged[key] = previous.get(key, "")
    entries = rmm_scripts.validate_entries(script, merged)
    return {key: value for key, value in entries.items() if value}


async def _scoped_script(script_id: Any, company_id: int | None) -> dict[str, Any]:
    try:
        script = await rmm_repo.get_script(int(script_id))
    except (TypeError, ValueError):
        script = None
    if company_id is None:
        if not script or not script.get("is_active"):
            raise RunRequestError({"script": "Choose a script."})
        if script.get("company_id") is not None:
            raise RunRequestError({"script": "Only scripts in Common/ can run for every company."})
        return script
    if not rmm_repo.script_available_to(script, company_id):
        raise RunRequestError({"script": "Choose a script."})
    return script  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Cron
# --------------------------------------------------------------------------- #


def default_timezone() -> str:
    return str(get_settings().default_timezone or "UTC")


def validate_timezone(name: Any) -> str:
    text = str(name or "").strip() or default_timezone()
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RunRequestError({"timezone": "Enter a time zone such as Australia/Sydney or UTC."}) from exc
    return text


def validate_cron(expression: Any) -> str:
    try:
        return cron_expression.validate(str(expression or ""))
    except ValueError as exc:
        raise RunRequestError({"cron": f"Check the schedule: {exc}."}) from exc


def next_runs(expression: str, tz_name: str, *, after: datetime | None = None, count: int = 1) -> list[datetime]:
    """The next ``count`` times (naive UTC) the cron expression fires in ``tz_name``."""

    zone = ZoneInfo(tz_name)
    start = (after or _utcnow()).replace(tzinfo=timezone.utc).astimezone(zone)
    times: list[datetime] = []
    try:
        iterator = croniter(cron_expression.for_croniter(expression), start)
        for _ in range(count):
            times.append(iterator.get_next(datetime).astimezone(timezone.utc).replace(tzinfo=None))
    except (CroniterBadDateError, ValueError, KeyError):
        pass
    return times


def next_run_after(expression: str, tz_name: str, after: datetime | None = None) -> datetime | None:
    times = next_runs(expression, tz_name, after=after)
    return times[0] if times else None


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #


def _int_list(values: Iterable[Any] | None, limit: int, key: str, label: str) -> list[int]:
    try:
        cleaned = list(dict.fromkeys(int(value) for value in values or []))
    except (TypeError, ValueError) as exc:
        raise RunRequestError({key: f"Choose {label} from the list."}) from exc
    if len(cleaned) > limit:
        raise RunRequestError({key: f"Choose at most {limit} {label}."})
    return cleaned


async def save_schedule(
    *,
    schedule_id: int | None,
    company_id: int | None,
    payload: Mapping[str, Any],
    user_id: int | None,
) -> int:
    """Create or update a schedule. ``company_id`` None makes it run for every company."""

    errors: dict[str, str] = {}
    name = str(payload.get("name") or "").strip()[:255]
    if not name:
        errors["name"] = "Give the schedule a name."
    script = await _scoped_script(payload.get("script_id"), company_id)
    cron = validate_cron(payload.get("cron"))
    tz_name = validate_timezone(payload.get("timezone"))
    target_mode = str(payload.get("target_mode") or "all")
    if target_mode not in TARGET_MODES or (target_mode == "assets" and company_id is None):
        errors["target_mode"] = "Choose which devices to run on."
    asset_ids = _int_list(payload.get("asset_ids"), MAX_SCHEDULE_ASSETS, "assets", "devices")
    tag_ids = _int_list(payload.get("tag_ids"), MAX_SCHEDULE_TAGS, "tags", "tags")
    if target_mode == "assets":
        for asset_id in asset_ids:
            asset = await assets_repo.get_asset_by_id(asset_id)
            if not asset or int(asset.get("company_id") or 0) != int(company_id or 0):
                errors["assets"] = "Choose devices from this company."
                break
        if not asset_ids:
            errors["assets"] = "Choose at least one device."
    else:
        asset_ids = []
    if target_mode == "tags":
        tag_ids = await tags_repo.existing_tag_ids(tag_ids)
        if not tag_ids:
            errors["tags"] = "Choose at least one tag."
    else:
        tag_ids = []

    previous: dict[str, str] = {}
    if schedule_id is not None:
        existing = await automation_repo.get_schedule(schedule_id, with_secrets=True)
        if existing and int(existing["script_id"]) == int(script["id"]):
            previous = decrypt_entries(existing.get("entries_encrypted"))
    try:
        entries = _merge_entries(script, payload.get("values") or {}, previous)
    except RunRequestError as exc:
        errors.update(exc.errors)
        entries = {}
    if errors:
        raise RunRequestError(errors)

    enabled = bool(payload.get("enabled", True))
    values = {
        "name": name,
        "company_id": company_id,
        "script_id": int(script["id"]),
        "cron": cron,
        "timezone": tz_name,
        "target_mode": target_mode,
        "asset_ids": asset_ids,
        "tag_ids": tag_ids,
        "entries_encrypted": encrypt_secret(json.dumps(entries)),
        "inputs": rmm_scripts.stored_inputs(script, entries),
        "timeout_seconds": rmm_scripts.clamp_timeout(
            payload.get("timeout_seconds"), int(script.get("default_timeout_seconds") or 600)
        ),
        "is_enabled": enabled,
        "next_run_at": next_run_after(cron, tz_name) if enabled else None,
        "created_by_user_id": user_id,
    }
    if schedule_id is None:
        return await automation_repo.create_schedule(values)
    await automation_repo.update_schedule(schedule_id, values)
    return schedule_id


async def set_schedule_enabled(schedule: Mapping[str, Any], enabled: bool) -> None:
    next_at = next_run_after(str(schedule["cron"]), str(schedule["timezone"])) if enabled else None
    await automation_repo.set_schedule_enabled(int(schedule["id"]), enabled, next_at)


async def schedule_targets(schedule: Mapping[str, Any], script: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Agents the schedule runs on: in scope, allowed the script, and matching its devices or tags."""

    company_id = schedule.get("company_id")
    agents = await automation_repo.list_active_agents(company_id=int(company_id) if company_id is not None else None)
    agents = [agent for agent in agents if rmm_repo.script_available_to(dict(script), int(agent["company_id"]))]
    mode = schedule.get("target_mode") or "all"
    if mode == "assets":
        wanted = {int(item) for item in schedule.get("asset_ids") or []}
        return [agent for agent in agents if agent.get("asset_id") is not None and int(agent["asset_id"]) in wanted]
    if mode == "tags":
        tagged = set(
            await tags_repo.list_asset_ids_with_tags(
                schedule.get("tag_ids") or [], company_id=int(company_id) if company_id is not None else None
            )
        )
        return [agent for agent in agents if agent.get("asset_id") is not None and int(agent["asset_id"]) in tagged]
    return agents


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


async def run_schedule(
    schedule_id: int, *, requested_by_user_id: int | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Queue the schedule's script on each of its devices and record a summary."""

    now = now or _utcnow()
    schedule = await automation_repo.get_schedule(schedule_id, with_secrets=True)
    if not schedule:
        return {"queued": [], "problems": [], "summary": "Schedule not found."}
    script = await rmm_repo.get_script(int(schedule["script_id"]), with_content=True)
    if not script or not script.get("is_active"):
        summary = "Not run: the script is no longer in Gitea."
        await automation_repo.record_schedule_run(schedule_id, summary)
        return {"queued": [], "problems": [], "summary": summary}

    entries = decrypt_entries(schedule.get("entries_encrypted"))
    inputs = schedule.get("inputs") or []
    timeout = rmm_scripts.clamp_timeout(schedule.get("timeout_seconds"), 600)
    next_at = schedule.get("next_run_at") if schedule.get("is_enabled") else None
    if isinstance(next_at, str):
        try:
            next_at = datetime.fromisoformat(next_at)
        except ValueError:
            next_at = None
    expires_at = now + rmm_scripts.QUEUE_LIFETIME
    if isinstance(next_at, datetime) and next_at > now:
        expires_at = min(expires_at, max(next_at, now + MIN_SCHEDULED_WAIT))

    pending = await automation_repo.pending_schedule_agents(schedule_id)
    queued: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    still_waiting = 0
    for agent in await schedule_targets(schedule, script):
        if int(agent["id"]) in pending:
            still_waiting += 1
            continue
        try:
            run_id = await rmm_scripts.queue_for_agent(
                script=script, agent=agent, entries=entries, inputs=inputs, timeout_seconds=timeout,
                requested_by_user_id=requested_by_user_id, run_source="schedule", schedule_id=schedule_id,
                expires_at=expires_at,
            )
        except RunRequestError as exc:
            problems.append({
                "agent_id": agent["id"], "asset_id": agent.get("asset_id"), "hostname": agent.get("hostname"),
                "message": "; ".join(exc.errors.values()),
            })
            continue
        queued.append({"run_id": run_id, "agent_id": agent["id"], "asset_id": agent.get("asset_id")})

    parts = [f"Queued on {_plural(len(queued), 'device')}"]
    if still_waiting:
        parts.append(f"{still_waiting} still busy with the last run")
    if problems:
        parts.append(f"{len(problems)} could not run it ({problems[0]['message']})")
    summary = "; ".join(parts) + "."
    await automation_repo.record_schedule_run(schedule_id, summary)
    log_info("RMM schedule ran", schedule_id=schedule_id, queued=len(queued), problems=len(problems))
    return {"queued": queued, "problems": problems, "summary": summary}


async def run_due_schedules(now: datetime | None = None) -> int:
    """Run every schedule whose time has come. Missed times are not caught up:
    a schedule that was due several times while MyPortal was down runs once."""

    now = now or _utcnow()
    ran = 0
    for row in await automation_repo.due_schedule_ids(now):
        schedule = await automation_repo.get_schedule(int(row["id"]))
        if not schedule:
            continue
        new_next = next_run_after(str(schedule["cron"]), str(schedule["timezone"]), after=now)
        if not await automation_repo.claim_schedule(int(row["id"]), row["next_run_at"], new_next):
            continue
        try:
            await run_schedule(int(row["id"]), now=now)
            ran += 1
        except Exception as exc:  # noqa: BLE001 - one bad schedule must not stop the rest
            log_warning("RMM schedule failed", schedule_id=row["id"], error=str(exc))
            await automation_repo.record_schedule_run(int(row["id"]), "Not run: an error occurred. See the logs.")
    return ran


# --------------------------------------------------------------------------- #
# Onboarding steps
# --------------------------------------------------------------------------- #


async def save_step(
    *,
    step_id: int | None,
    company_id: int,
    payload: Mapping[str, Any],
    user_id: int | None,
) -> int:
    """Create or update one of a company's onboarding steps. A step needs at
    least one tag: it runs only on devices with any of them."""

    errors: dict[str, str] = {}
    script = await _scoped_script(payload.get("script_id"), int(company_id))
    tag_ids = await tags_repo.existing_tag_ids(
        _int_list(payload.get("tag_ids"), MAX_SCHEDULE_TAGS, "tags", "tags")
    )
    if not tag_ids:
        errors["tags"] = "Choose at least one tag, such as Server or Workstation."
    previous: dict[str, str] = {}
    if step_id is not None:
        existing = await automation_repo.get_step(step_id, with_secrets=True)
        if existing and int(existing["script_id"]) == int(script["id"]):
            previous = decrypt_entries(existing.get("entries_encrypted"))
    try:
        entries = _merge_entries(script, payload.get("values") or {}, previous)
    except RunRequestError as exc:
        errors.update(exc.errors)
        entries = {}
    if errors:
        raise RunRequestError(errors)
    values = {
        "company_id": int(company_id),
        "script_id": int(script["id"]),
        "tag_ids": tag_ids,
        "entries_encrypted": encrypt_secret(json.dumps(entries)),
        "inputs": rmm_scripts.stored_inputs(script, entries),
        "timeout_seconds": rmm_scripts.clamp_timeout(
            payload.get("timeout_seconds"), int(script.get("default_timeout_seconds") or 600)
        ),
        "continue_on_failure": bool(payload.get("continue_on_failure")),
        "created_by_user_id": user_id,
    }
    if step_id is None:
        return await automation_repo.create_step(values)
    await automation_repo.update_step(step_id, values)
    return step_id


async def reorder_steps(company_id: int, step_ids: Iterable[Any]) -> None:
    current = [int(step["id"]) for step in await automation_repo.list_steps(company_id=company_id)]
    try:
        wanted = [int(step_id) for step_id in step_ids]
    except (TypeError, ValueError) as exc:
        raise RunRequestError({"order": "Refresh the page and try again."}) from exc
    if sorted(wanted) != sorted(current) or len(set(wanted)) != len(wanted):
        raise RunRequestError({"order": "The list changed since the page loaded. Refresh and try again."})
    await automation_repo.set_step_positions(wanted)


async def tag_names() -> dict[int, str]:
    return {int(tag["id"]): str(tag["name"]) for tag in await tags_repo.list_tags()}


async def build_plan(company_id: int) -> list[dict[str, Any]]:
    """The company's steps, in order, as a fresh onboarding plan."""

    names = await tag_names()
    return [
        {
            "step_id": int(step["id"]),
            "script_id": int(step["script_id"]),
            "script_name": step.get("script_name") or "Script",
            "tags": [names[tag_id] for tag_id in step.get("tag_ids") or [] if tag_id in names],
            "continue_on_failure": bool(step.get("continue_on_failure")),
            "status": "pending",
            "run_id": None,
            "message": None,
        }
        for step in await automation_repo.list_steps(company_id=company_id)
    ]


# --------------------------------------------------------------------------- #
# Onboarding runs
# --------------------------------------------------------------------------- #


class _StepSkipped(Exception):
    """The step does not apply to this device (its tags do not match, or it
    was deleted after the sequence started)."""


async def start_onboarding(agent: Mapping[str, Any], *, started_by_user_id: int | None = None) -> int | None:
    """Start the onboarding sequence on ``agent``'s device. Returns its id, or
    ``None`` when there are no steps. Raises ``ValueError`` if one is running."""

    if agent.get("company_id") is None:
        return None
    if await automation_repo.agent_has_running_onboarding(int(agent["id"])):
        raise ValueError("Onboarding is already running on this device.")
    plan = await build_plan(int(agent["company_id"]))
    if not plan:
        return None
    onboarding_id = await automation_repo.create_onboarding_run(
        agent_id=int(agent["id"]),
        company_id=int(agent["company_id"]),
        asset_id=int(agent["asset_id"]) if agent.get("asset_id") is not None else None,
        plan=plan,
        started_by_user_id=started_by_user_id,
    )
    log_info("RMM onboarding started", onboarding_run_id=onboarding_id, agent_id=agent["id"], steps=len(plan))
    await advance_onboarding(onboarding_id)
    return onboarding_id


async def on_agent_enrolled(agent_id: int) -> int | None:
    """Start onboarding for a newly enrolled agent. Never raises: enrolment must succeed."""

    try:
        agent = await rmm_repo.get_agent(agent_id)
        return await start_onboarding(agent) if agent else None
    except Exception as exc:  # noqa: BLE001 - logged; the agent is enrolled either way
        log_warning("RMM onboarding could not start", agent_id=agent_id, error=str(exc))
        return None


async def device_matches_tags(agent: Mapping[str, Any], tag_ids: list[int]) -> bool:
    """True when the agent's asset (or its company) carries any of ``tag_ids``.
    Automatic tags such as Server or Workstation are refreshed first, since a
    newly enrolled device may not have them yet."""

    asset_id = agent.get("asset_id")
    if asset_id is None or not tag_ids:
        return False
    try:
        await tags_repo.refresh_asset_auto_tags(int(asset_id))
    except Exception as exc:  # noqa: BLE001 - match on the tags it already has
        log_warning("Automatic tags could not be refreshed", asset_id=asset_id, error=str(exc))
    matching = await tags_repo.list_asset_ids_with_tags(tag_ids, company_id=int(agent["company_id"]))
    return int(asset_id) in matching


async def _queue_step(onboarding: Mapping[str, Any], entry: Mapping[str, Any]) -> int:
    agent = await rmm_repo.get_agent(int(onboarding["agent_id"])) if onboarding.get("agent_id") else None
    if not agent or agent.get("status") != "active":
        raise RunRequestError({"device": "The device's RMM agent is no longer enrolled."})
    step = await automation_repo.get_step(int(entry["step_id"]), with_secrets=True)
    if not step:
        raise _StepSkipped("Removed from the onboarding list.")
    if not await device_matches_tags(agent, step.get("tag_ids") or []):
        raise _StepSkipped("The device has none of this step's tags.")
    script = await rmm_repo.get_script(int(step["script_id"]), with_content=True)
    if not rmm_repo.script_available_to(script, int(onboarding["company_id"])):
        raise RunRequestError({"script": "The script is no longer available to this company."})
    entries = rmm_scripts.validate_entries(script, decrypt_entries(step.get("entries_encrypted")))
    return await rmm_scripts.queue_for_agent(
        script=script,  # type: ignore[arg-type]
        agent={**agent, "company_id": int(onboarding["company_id"])},
        entries=entries,
        inputs=rmm_scripts.stored_inputs(script, entries),  # type: ignore[arg-type]
        timeout_seconds=rmm_scripts.clamp_timeout(step.get("timeout_seconds"), 600),
        requested_by_user_id=onboarding.get("started_by_user_id"),
        run_source="onboarding",
        onboarding_run_id=int(onboarding["id"]),
        notify=False,
    )


def _stop_message(index: int, entry: Mapping[str, Any], outcome: str) -> str:
    return f"Step {index + 1} ({entry.get('script_name')}) {outcome}, so the remaining steps were not run."


def _mark_rest(plan: list[dict[str, Any]], start: int) -> None:
    for entry in plan[start:]:
        if entry.get("status") == "pending":
            entry["status"] = "not_run"


async def advance_onboarding(onboarding_run_id: int) -> None:
    """Record the current step's outcome and queue the next one, or finish.

    Safe to call at any time and from several workers: it does nothing while
    the current step is still running, and only one caller moves a sequence on.
    """

    onboarding = await automation_repo.get_onboarding_run(onboarding_run_id)
    if not onboarding or onboarding.get("status") != automation_repo.ONBOARDING_ACTIVE:
        return
    plan: list[dict[str, Any]] = list(onboarding.get("plan") or [])
    index = int(onboarding.get("current_index") if onboarding.get("current_index") is not None else -1)
    current_run_id = onboarding.get("current_run_id")
    failed = int(onboarding.get("failed_steps") or 0)

    if 0 <= index < len(plan) and current_run_id is not None:
        run = await rmm_repo.get_run(int(current_run_id))
        status = run["status"] if run else "cancelled"
        if status in rmm_repo.ACTIVE_RUN_STATUSES:
            return
        entry = plan[index]
        entry["status"] = status
        entry["message"] = (run or {}).get("error_message")
        if status != "completed":
            failed += 1
            if not entry.get("continue_on_failure"):
                _mark_rest(plan, index + 1)
                await automation_repo.finish_onboarding_run(
                    onboarding_run_id, status="failed", plan=plan, failed_steps=failed,
                    error_message=_stop_message(index, entry, _FINISH_WORDS.get(status, status)),
                    expected_index=index,
                )
                return

    next_index = index + 1
    while next_index < len(plan):
        entry = plan[next_index]
        try:
            run_id = await _queue_step(onboarding, entry)
        except _StepSkipped as exc:
            entry["status"], entry["message"] = "skipped", str(exc)
            next_index += 1
            continue
        except RunRequestError as exc:
            message = "; ".join(exc.errors.values())
            entry["status"], entry["message"] = "failed", message
            failed += 1
            if not entry.get("continue_on_failure"):
                _mark_rest(plan, next_index + 1)
                await automation_repo.finish_onboarding_run(
                    onboarding_run_id, status="failed", plan=plan, failed_steps=failed,
                    error_message=_stop_message(next_index, entry, f"could not run ({message})"),
                    expected_index=index,
                )
                return
            next_index += 1
            continue
        entry["status"], entry["run_id"] = "queued", run_id
        moved = await automation_repo.advance_onboarding_run(
            onboarding_run_id, expected_index=index, expected_run_id=current_run_id,
            new_index=next_index, new_run_id=run_id, plan=plan, failed_steps=failed,
        )
        if moved:
            rmm_scripts.notify_agent(int(onboarding["agent_id"]))
        else:
            # Another worker moved this sequence on first; drop the duplicate.
            await rmm_repo.cancel_run(run_id)
        return

    await automation_repo.finish_onboarding_run(
        onboarding_run_id, status="completed", plan=plan, failed_steps=failed,
        error_message=f"{_plural(failed, 'step')} failed." if failed else None,
        expected_index=index,
    )
    log_info("RMM onboarding finished", onboarding_run_id=onboarding_run_id, failed_steps=failed)


async def run_finished(run_id: int) -> None:
    """Move an onboarding sequence on after one of its runs finished."""

    run = await rmm_repo.get_run(run_id)
    if run and run.get("onboarding_run_id"):
        await advance_onboarding(int(run["onboarding_run_id"]))


async def cancel_onboarding(onboarding: Mapping[str, Any]) -> bool:
    plan = list(onboarding.get("plan") or [])
    _mark_rest(plan, 0)
    cancelled = await automation_repo.finish_onboarding_run(
        int(onboarding["id"]), status="cancelled", plan=plan, failed_steps=int(onboarding.get("failed_steps") or 0),
        error_message="Cancelled by a technician.",
    )
    if cancelled and onboarding.get("current_run_id"):
        await rmm_repo.cancel_run(int(onboarding["current_run_id"]))
    return cancelled


async def reconcile_onboarding() -> None:
    for onboarding_id in await automation_repo.running_onboarding_ids():
        try:
            await advance_onboarding(onboarding_id)
        except Exception as exc:  # noqa: BLE001 - one bad sequence must not stop the rest
            log_warning("RMM onboarding check failed", onboarding_run_id=onboarding_id, error=str(exc))


# --------------------------------------------------------------------------- #
# Background loop
# --------------------------------------------------------------------------- #


@singleton_run("rmm_automation", ttl_seconds=TICK_SECONDS * 3)
async def tick() -> None:
    await rmm_repo.expire_stale_runs()
    await reconcile_onboarding()
    await run_due_schedules()


async def automation_loop() -> None:
    """Run due schedules and move onboarding sequences on, on one instance at a time."""

    await asyncio.sleep(10 + random.uniform(0, 10))  # nosec B311 - start-up jitter, not security
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - logged and retried
            log_warning("RMM automation check failed", error=str(exc))
        await asyncio.sleep(TICK_SECONDS)
