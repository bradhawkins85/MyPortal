"""Export and import of event automations as self-contained JSON documents."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.schemas.automations import AutomationCreate

EXPORT_FORMAT = "myportal.automation"
EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 1_000_000

_EXPORTED_FIELDS = (
    "name",
    "description",
    "kind",
    "trigger_event",
    "trigger_filters",
    "action_module",
    "action_payload",
    "business_hours_mode",
    "business_hours_source",
    "status",
)


class AutomationImportError(ValueError):
    """Raised when an uploaded automation document cannot be imported."""


def build_export(automation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the portable JSON document for an event automation."""

    if str(automation.get("kind") or "").lower() != "event":
        raise AutomationImportError("Only event automations can be exported.")
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "automation": {field: automation.get(field) for field in _EXPORTED_FIELDS},
    }


def export_filename(automation: Mapping[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(automation.get("name") or "").lower()).strip("-")
    return f"automation-{slug or automation.get('id') or 'export'}.json"


def _check_modules(data: Mapping[str, Any]) -> None:
    from app.services.component_availability import get_component_availability

    modules = {str(data["action_module"])} if data.get("action_module") else set()
    payload = data.get("action_payload")
    if isinstance(payload, Mapping) and isinstance(payload.get("actions"), list):
        modules.update(
            str(action.get("module"))
            for action in payload["actions"]
            if isinstance(action, Mapping) and action.get("module")
        )
    availability = get_component_availability()
    unavailable = sorted(slug for slug in modules if not availability.module_available(slug))
    if unavailable:
        raise AutomationImportError(
            "Automation references module(s) unavailable in this deployment: "
            + ", ".join(unavailable)
        )


def _check_step_flow(data: Mapping[str, Any]) -> None:
    from app.services import automations as automation_service

    payload = data.get("action_payload")
    actions = payload.get("actions") if isinstance(payload, Mapping) else None
    if not isinstance(actions, list):
        return
    try:
        for action in actions:
            if isinstance(action, Mapping):
                automation_service.normalise_step_output_var(action.get("output_var"))
                automation_service.normalise_step_condition(action.get("run_if"))
        automation_service.validate_step_flow(automation_service._normalise_actions(actions))
    except ValueError as exc:
        raise AutomationImportError(str(exc)) from exc


def parse_import(document: Any) -> dict[str, Any]:
    """Validate an exported document and return the fields to create.

    Imported automations always start inactive so they can be reviewed
    (IDs of companies, users or mailboxes may differ between portals)
    before they run.
    """

    if not isinstance(document, Mapping):
        raise AutomationImportError("The file is not an exported automation.")
    if document.get("format") != EXPORT_FORMAT:
        raise AutomationImportError("The file is not a MyPortal automation export.")
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version > EXPORT_VERSION:
        raise AutomationImportError("This automation export version is not supported.")
    automation = document.get("automation")
    if not isinstance(automation, Mapping):
        raise AutomationImportError("The export does not contain an automation.")
    if str(automation.get("kind") or "").lower() != "event":
        raise AutomationImportError("Only event automations can be imported.")

    candidate = {field: automation.get(field) for field in _EXPORTED_FIELDS if field != "status"}
    candidate["kind"] = "event"
    candidate["status"] = "inactive"
    try:
        validated = AutomationCreate(**candidate)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = first.get("msg", "invalid value")
        raise AutomationImportError(
            "The automation is invalid" + (" (" + location + ")" if location else "") + ": " + message
        ) from exc
    if not (validated.trigger_event or "").strip():
        raise AutomationImportError("The automation has no trigger event.")

    data = validated.model_dump()
    _check_modules(data)
    _check_step_flow(data)
    return {
        "name": data["name"],
        "description": data.get("description"),
        "kind": "event",
        "execution_order": 0,
        "cadence": None,
        "cron_expression": None,
        "scheduled_time": None,
        "run_once": False,
        "trigger_event": data["trigger_event"],
        "trigger_filters": data.get("trigger_filters"),
        "action_module": data.get("action_module"),
        "action_payload": data.get("action_payload"),
        "status": "inactive",
        "business_hours_mode": data.get("business_hours_mode"),
        "business_hours_source": data.get("business_hours_source"),
    }


def unique_import_name(name: str, existing_names: set[str]) -> str:
    if name not in existing_names:
        return name
    name = name[:230].rstrip()
    candidate = f"{name} (imported)"
    suffix = 2
    while candidate in existing_names:
        candidate = f"{name} (imported {suffix})"
        suffix += 1
    return candidate


async def import_automation(document: Any) -> dict[str, Any]:
    """Validate ``document`` and create it as a new inactive automation."""

    from app.repositories import automations as automation_repo

    data = parse_import(document)
    existing = await automation_repo.list_automations(limit=1000)
    existing_names = {str(item.get("name")) for item in existing if item.get("name")}
    data["name"] = unique_import_name(data["name"], existing_names)
    return await automation_repo.create_automation(next_run_at=None, **data)
