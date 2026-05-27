"""Automations handlers for the ``automations`` feature pack."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData


def _main():
    from app import main as main_module

    return main_module


async def _render_automations_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    status_filter: str | None = None,
    kind_filter: str | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    from app.repositories import automations as automation_repo

    automations = await automation_repo.list_automations(
        status=status_filter,
        kind=kind_filter,
        limit=200,
    )
    status_counts = Counter((automation.get("status") or "inactive").lower() for automation in automations)
    kind_counts = Counter((automation.get("kind") or "scheduled").lower() for automation in automations)
    extra = {
        "title": "Automation orchestration",
        "automations": automations,
        "automation_status_counts": status_counts,
        "automation_kind_counts": kind_counts,
        "automation_filters": {"status": status_filter, "kind": kind_filter},
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await _main()._render_template("admin/automations.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_automation_form(
    request: Request,
    user: dict[str, Any],
    *,
    kind: str,
    form_values: Mapping[str, Any] | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
    mode: str = "create",
    automation_id: int | None = None,
):
    from app.services import automations as automations_service
    from app.services import modules as modules_service

    kind_normalised = "event" if str(kind).lower() == "event" else "scheduled"
    modules = await modules_service.list_trigger_action_modules()
    modules_payload = _main()._serialise_for_json(modules)
    trigger_options = automations_service.list_trigger_events()
    mode_normalised = "edit" if str(mode).lower() == "edit" else "create"
    is_edit_mode = mode_normalised == "edit"
    base_values: dict[str, Any] = {
        "name": "",
        "description": "",
        "status": "inactive",
        "cadence": "",
        "cronExpression": "",
        "triggerEvent": "",
        "triggerFiltersRaw": "",
        "actionModule": "",
        "actionPayloadRaw": "",
    }
    if form_values:
        for key, value in form_values.items():
            if value is None:
                continue
            base_values[key] = value
    trigger_event = str(base_values.get("triggerEvent") or "").strip()
    option_values = {str(option.get("value") or "") for option in trigger_options}
    if trigger_event and trigger_event not in option_values:
        trigger_select_value = "__custom__"
        trigger_custom_value = trigger_event
    else:
        trigger_select_value = trigger_event
        trigger_custom_value = ""
    base_values["triggerSelectValue"] = trigger_select_value
    base_values["triggerCustomValue"] = trigger_custom_value
    if automation_id is not None:
        base_values.setdefault("id", automation_id)
    template_name = (
        "admin/automations_create_event.html"
        if kind_normalised == "event"
        else "admin/automations_create_scheduled.html"
    )
    if kind_normalised == "event":
        page_title = "Edit event automation" if is_edit_mode else "Create event automation"
        page_subtitle = (
            "Link webhook payloads and application events to integration modules for immediate processing."
        )
        alternate_link = None
        if not is_edit_mode:
            alternate_link = {
                "url": "/admin/automations/create/scheduled",
                "label": "Switch to scheduled automation",
            }
    else:
        page_title = "Edit scheduled automation" if is_edit_mode else "Create scheduled automation"
        page_subtitle = (
            "Configure cadence, triggers, and action payloads to run on a predictable rhythm."
        )
        alternate_link = None
        if not is_edit_mode:
            alternate_link = {
                "url": "/admin/automations/create/event",
                "label": "Switch to event automation",
            }
    form_action = (
        f"/admin/automations/{automation_id}"
        if is_edit_mode and automation_id is not None
        else "/admin/automations"
    )
    submit_label = "Update automation" if is_edit_mode else "Save automation"
    extra = {
        "title": page_title,
        "automation_modules": modules_payload,
        "automation_trigger_options": trigger_options,
        "form_values": base_values,
        "kind": kind_normalised,
        "success_message": success_message,
        "error_message": error_message,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
        "alternate_link": alternate_link,
        "form_action": form_action,
        "submit_label": submit_label,
        "is_edit_mode": is_edit_mode,
        "automation_id": automation_id,
    }
    response = await _main()._render_template(template_name, request, user, extra=extra)
    response.status_code = status_code
    return response


def _automation_to_form_values(automation: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": str(automation.get("name") or ""),
        "description": str(automation.get("description") or ""),
        "status": str(automation.get("status") or "inactive"),
        "executionOrder": int(automation.get("execution_order") or 0),
        "cadence": str(automation.get("cadence") or ""),
        "cronExpression": str(automation.get("cron_expression") or ""),
        "runOnce": bool(automation.get("run_once", False)),
        "scheduledTime": "",
        "triggerEvent": str(automation.get("trigger_event") or ""),
        "triggerFiltersRaw": "",
        "actionModule": str(automation.get("action_module") or ""),
        "actionPayloadRaw": "",
    }
    scheduled_time = automation.get("scheduled_time")
    if scheduled_time and isinstance(scheduled_time, datetime):
        local_time = scheduled_time.astimezone()
        values["scheduledTime"] = local_time.strftime("%Y-%m-%dT%H:%M")
    filters = automation.get("trigger_filters")
    if filters is not None:
        try:
            values["triggerFiltersRaw"] = json.dumps(filters, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            values["triggerFiltersRaw"] = json.dumps(filters, default=str)
    payload = automation.get("action_payload")
    if payload is not None:
        try:
            values["actionPayloadRaw"] = json.dumps(payload, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            values["actionPayloadRaw"] = json.dumps(payload, default=str)
    return values


def _parse_automation_form_submission(
    form: FormData,
    *,
    kind: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], str | None, int]:
    from app.services import modules as modules_service

    def _get_str_value(key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value)

    kind_normalised = "event" if str(kind).lower() == "event" else "scheduled"
    name = _get_str_value("name").strip()
    description_value = _get_str_value("description").strip()
    status_raw = _get_str_value("status").strip().lower()
    status_value = "active" if status_raw == "active" else "inactive"
    execution_order_raw = _get_str_value("executionOrder").strip()
    try:
        execution_order = max(0, int(execution_order_raw)) if execution_order_raw else 0
    except (ValueError, TypeError):
        execution_order = 0
    cadence_raw = _get_str_value("cadence").strip()
    cron_raw = _get_str_value("cronExpression").strip()
    run_once_raw = _get_str_value("runOnce").strip().lower()
    run_once = run_once_raw in ("true", "1", "yes", "on")
    scheduled_time_raw = _get_str_value("scheduledTime").strip()
    trigger_event_raw = _get_str_value("triggerEvent").strip()
    trigger_filters_raw = _get_str_value("triggerFilters").strip()
    trigger_filters_mode_raw = _get_str_value("triggerFiltersMode").strip().lower()
    trigger_filters_mode = "advanced" if trigger_filters_mode_raw == "advanced" else "builder"
    action_module_raw = _get_str_value("actionModule").strip()
    action_payload_raw = _get_str_value("actionPayload").strip()

    form_state = {
        "name": name,
        "description": description_value,
        "status": status_value,
        "executionOrder": execution_order,
        "cadence": cadence_raw,
        "cronExpression": cron_raw,
        "runOnce": run_once,
        "scheduledTime": scheduled_time_raw,
        "triggerEvent": trigger_event_raw,
        "triggerFiltersRaw": trigger_filters_raw,
        "triggerFiltersMode": trigger_filters_mode,
        "actionModule": action_module_raw,
        "actionPayloadRaw": action_payload_raw,
    }

    if not name:
        return None, form_state, "Enter an automation name.", status.HTTP_400_BAD_REQUEST

    cadence = cadence_raw or None
    cron_expression = cron_raw or None
    trigger_event = trigger_event_raw or None
    action_module = action_module_raw or None

    scheduled_time = None
    if kind_normalised == "scheduled" and run_once:
        if not scheduled_time_raw:
            return (
                None,
                form_state,
                "Scheduled time is required for one-time automations.",
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            scheduled_time = datetime.fromisoformat(scheduled_time_raw)
            if scheduled_time.tzinfo is None:
                local_tz = datetime.now().astimezone().tzinfo
                scheduled_time = scheduled_time.replace(tzinfo=local_tz).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return (
                None,
                form_state,
                "Invalid scheduled time format. Use YYYY-MM-DDTHH:MM format.",
                status.HTTP_400_BAD_REQUEST,
            )

    try:
        trigger_filters = json.loads(trigger_filters_raw) if trigger_filters_raw else None
    except json.JSONDecodeError:
        invalid_section = "Advanced JSON trigger filters" if trigger_filters_mode == "advanced" else "Trigger filter builder payload"
        return (
            None,
            form_state,
            f"{invalid_section} is invalid JSON.",
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        action_payload = json.loads(action_payload_raw) if action_payload_raw else None
    except json.JSONDecodeError:
        return (
            None,
            form_state,
            "Action payload must be valid JSON.",
            status.HTTP_400_BAD_REQUEST,
        )

    normalised_actions: list[dict[str, Any]] = []
    if isinstance(action_payload, dict) and "actions" in action_payload:
        actions_value = action_payload.get("actions")
        if not isinstance(actions_value, list):
            return (
                None,
                form_state,
                "Trigger actions must be provided as a list.",
                status.HTTP_400_BAD_REQUEST,
            )
        for index, entry in enumerate(actions_value, start=1):
            if not isinstance(entry, dict):
                return (
                    None,
                    form_state,
                    f"Trigger action {index} is invalid.",
                    status.HTTP_400_BAD_REQUEST,
                )
            module_value = str(entry.get("module") or "").strip()
            if not module_value:
                return (
                    None,
                    form_state,
                    f"Select an action module for trigger action {index}.",
                    status.HTTP_400_BAD_REQUEST,
                )
            payload_value = entry.get("payload") or {}
            if not isinstance(payload_value, dict):
                return (
                    None,
                    form_state,
                    f"Trigger action {index} payload must be an object.",
                    status.HTTP_400_BAD_REQUEST,
                )
            try:
                modules_service.validate_action_payload(module_value, payload_value)
            except ValueError as exc:
                return (
                    None,
                    form_state,
                    f"Trigger action {index}: {exc}",
                    status.HTTP_400_BAD_REQUEST,
                )
            action_entry: dict[str, Any] = {"module": module_value, "payload": payload_value}
            raw_order = entry.get("order")
            if raw_order is not None:
                try:
                    action_entry["order"] = int(raw_order)
                except (TypeError, ValueError):
                    pass
            note_value = str(entry.get("note") or "").strip()
            if note_value:
                action_entry["note"] = note_value
            normalised_actions.append(action_entry)
        updated_payload = dict(action_payload)
        updated_payload["actions"] = normalised_actions
        action_payload = updated_payload
        action_module = normalised_actions[0]["module"] if normalised_actions else None
        form_state["actionPayloadRaw"] = json.dumps(action_payload)
        form_state["actionModule"] = action_module or ""
    elif action_module and isinstance(action_payload, dict):
        try:
            modules_service.validate_action_payload(action_module, action_payload)
        except ValueError as exc:
            return (
                None,
                form_state,
                str(exc),
                status.HTTP_400_BAD_REQUEST,
            )

    data = {
        "name": name,
        "description": description_value or None,
        "kind": kind_normalised,
        "execution_order": execution_order,
        "cadence": cadence if kind_normalised == "scheduled" else None,
        "cron_expression": cron_expression if kind_normalised == "scheduled" else None,
        "scheduled_time": scheduled_time,
        "run_once": run_once,
        "trigger_event": trigger_event,
        "trigger_filters": trigger_filters,
        "action_module": action_module,
        "action_payload": action_payload,
        "status": status_value,
    }

    return data, form_state, None, status.HTTP_200_OK


async def admin_automations_page(
    request: Request,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_automations_dashboard(
        request,
        current_user,
        status_filter=status,
        kind_filter=kind,
        success_message=_main()._sanitize_message(success),
        error_message=_main()._sanitize_message(error),
    )


async def admin_create_scheduled_automation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_automation_form(
        request,
        current_user,
        kind="scheduled",
        success_message=_main()._sanitize_message(success),
        error_message=_main()._sanitize_message(error),
    )


async def admin_create_event_automation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_automation_form(
        request,
        current_user,
        kind="event",
        success_message=_main()._sanitize_message(success),
        error_message=_main()._sanitize_message(error),
    )


async def admin_create_automation(request: Request):
    from app.core.logging import log_error
    from app.repositories import automations as automation_repo
    from app.services import automations as automations_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    kind_raw = str(form.get("kind", "")).strip()
    kind = "event" if kind_raw.lower() == "event" else "scheduled"
    data, form_state, error_message, error_status = _parse_automation_form_submission(form, kind=kind)
    if error_message:
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message=error_message,
            status_code=error_status,
        )
    next_run = None
    if data.get("status") == "active":
        next_run = automations_service.calculate_next_run(data)
    try:
        record = await automation_repo.create_automation(next_run_at=next_run, **data)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create automation", error=str(exc))
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message="Unable to create automation. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if record and record.get("status") == "active":
        await automations_service.refresh_schedule(int(record["id"]))
    return RedirectResponse(
        url="/admin/automations?success=" + quote("Automation created."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_edit_automation_page(automation_id: int, request: Request):
    from app.repositories import automations as automation_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        return RedirectResponse(
            url="/admin/automations?error=" + quote("Automation not found."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    kind = str(automation.get("kind") or "scheduled")
    form_defaults = _automation_to_form_values(automation)
    return await _render_automation_form(
        request,
        current_user,
        kind=kind,
        form_values=form_defaults,
        mode="edit",
        automation_id=automation_id,
    )


async def admin_update_automation(automation_id: int, request: Request):
    from app.core.logging import log_error
    from app.repositories import automations as automation_repo
    from app.services import automations as automations_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        return RedirectResponse(
            url="/admin/automations?error=" + quote("Automation not found."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    form = await request.form()
    kind = str(automation.get("kind") or "scheduled")
    data, form_state, error_message, error_status = _parse_automation_form_submission(form, kind=kind)
    if error_message:
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message=error_message,
            status_code=error_status,
            mode="edit",
            automation_id=automation_id,
        )
    update_fields = dict(data)
    if update_fields.get("status") != "active":
        update_fields["next_run_at"] = None
    try:
        await automation_repo.update_automation(automation_id, **update_fields)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update automation", automation_id=automation_id, error=str(exc))
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message="Unable to update automation. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            mode="edit",
            automation_id=automation_id,
        )
    if update_fields.get("status") == "active":
        await automations_service.refresh_schedule(automation_id)
    else:
        await automation_repo.set_next_run(automation_id, None)
    return RedirectResponse(
        url="/admin/automations?success=" + quote("Automation updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_update_automation_status(automation_id: int, request: Request):
    from app.repositories import automations as automation_repo
    from app.services import automations as automations_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    status_value = str(form.get("status", "")).strip()
    if status_value not in {"active", "inactive"}:
        return await _render_automations_dashboard(
            request,
            current_user,
            error_message="Select a valid automation status.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    await automation_repo.update_automation(automation_id, status=status_value)
    await automations_service.refresh_schedule(automation_id)
    return RedirectResponse(
        url="/admin/automations?success=" + quote(f"Automation {automation_id} updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_execute_automation(automation_id: int, request: Request):
    from app.services import automations as automations_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        result = await automations_service.execute_now(automation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    message = f"Automation {automation_id} executed with status {result.get('status')}."
    return RedirectResponse(
        url="/admin/automations?success=" + quote(message),
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_delete_automation(automation_id: int, request: Request):
    from app.core.logging import log_error, log_info
    from app.repositories import automations as automation_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")

    try:
        await automation_repo.delete_automation(automation_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to delete automation",
            automation_id=automation_id,
            error=str(exc),
        )
        return await _render_automations_dashboard(
            request,
            current_user,
            error_message="Unable to delete the automation. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    log_info(
        "Automation deleted",
        automation_id=automation_id,
        deleted_by=current_user.get("id") if isinstance(current_user, Mapping) else None,
    )

    message = quote(f"Automation {automation_id} deleted.")
    return RedirectResponse(
        url=f"/admin/automations?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
