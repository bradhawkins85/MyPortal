from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from app.core.notifications import DEFAULT_NOTIFICATION_EVENTS, merge_event_types
from app.repositories import notification_event_settings as settings_repo


_BOOL_FIELDS = {
    "is_user_visible": True,
    "allow_channel_in_app": True,
    "allow_channel_email": False,
    "allow_channel_sms": False,
    "default_channel_in_app": True,
    "default_channel_email": False,
    "default_channel_sms": False,
}


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _ensure_module_actions(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    actions: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        value = [value]
    if isinstance(value, (list, tuple)):
        for entry in value:
            if not isinstance(entry, Mapping):
                continue
            module = str(entry.get("module") or "").strip()
            if not module:
                continue
            payload = entry.get("payload")
            if isinstance(payload, Mapping):
                payload_value: Any = dict(payload)
            elif isinstance(payload, (list, tuple)):
                payload_value = list(payload)
            else:
                payload_value = payload if payload is not None else {}
            actions.append({"module": module, "payload": payload_value})
    return actions


def _merge_setting(event_type: str, overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(DEFAULT_NOTIFICATION_EVENTS.get(event_type, {}))
    merged: dict[str, Any] = {"event_type": event_type}

    display_name = str(base.get("display_name") or event_type)
    description = base.get("description")
    message_template = str(base.get("message_template") or "{{ message }}")
    module_actions = _ensure_module_actions(base.get("module_actions"))

    if overrides:
        display_name = str(overrides.get("display_name") or display_name)
        if overrides.get("description") is not None:
            description = str(overrides.get("description") or "") or None
        message_template = str(overrides.get("message_template") or message_template)
        module_actions = _ensure_module_actions(overrides.get("module_actions") or module_actions)

    merged["display_name"] = display_name.strip() or event_type
    merged["description"] = description
    merged["message_template"] = message_template.strip() or "{{ message }}"
    merged["module_actions"] = module_actions

    for field, default in _BOOL_FIELDS.items():
        base_value = _coerce_bool(base.get(field), default) if base else default
        override_value = overrides.get(field) if overrides else base_value
        merged[field] = _coerce_bool(override_value, base_value)

    return merged


async def list_event_settings(include_hidden: bool = True) -> list[dict[str, Any]]:
    stored = await settings_repo.list_settings()
    stored_map = {item["event_type"]: item for item in stored if item.get("event_type")}
    event_types = merge_event_types(DEFAULT_NOTIFICATION_EVENTS.keys(), stored_map.keys())
    results: list[dict[str, Any]] = []
    for event_type in event_types:
        overrides = stored_map.get(event_type)
        setting = _merge_setting(event_type, overrides)
        if not include_hidden and not setting.get("is_user_visible", True):
            continue
        results.append(setting)
    results.sort(key=lambda item: (item.get("display_name") or item.get("event_type") or "").lower())
    return results


async def get_event_setting(event_type: str) -> dict[str, Any]:
    stored = await settings_repo.get_setting(event_type)
    return _merge_setting(event_type, stored)


async def update_event_setting(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    updated = await settings_repo.upsert_setting(event_type, payload)
    return _merge_setting(event_type, updated)


async def reconcile_known_events(additional_events: Iterable[str] | None = None) -> None:
    """Ensure rows exist only for known events to prevent orphaned records."""

    known_events = merge_event_types(DEFAULT_NOTIFICATION_EVENTS.keys(), additional_events)
    await settings_repo.delete_missing(known_events)
