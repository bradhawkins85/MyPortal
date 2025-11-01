from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.core.database import db


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _normalise_actions(actions: Any) -> list[dict[str, Any]]:
    if isinstance(actions, str):
        try:
            parsed = json.loads(actions)
        except json.JSONDecodeError:
            return []
        return _normalise_actions(parsed)
    if not isinstance(actions, Iterable) or isinstance(actions, (bytes, bytearray, str)):
        return []
    normalised: list[dict[str, Any]] = []
    for entry in actions:
        if not isinstance(entry, Mapping):
            continue
        module = _coerce_text(entry.get("module"), default="").strip()
        if not module:
            continue
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            payload_value = dict(payload)
        elif isinstance(payload, (list, tuple)):
            payload_value = list(payload)
        else:
            payload_value = payload if payload is not None else {}
        normalised.append({"module": module, "payload": payload_value})
    return normalised


def _serialise_actions(actions: Any) -> str | None:
    normalised = _normalise_actions(actions)
    if not normalised:
        return None
    try:
        return json.dumps(normalised)
    except (TypeError, ValueError):
        serialisable: list[dict[str, Any]] = []
        for entry in normalised:
            payload = entry.get("payload")
            try:
                json.dumps(payload)
                serialisable.append(entry)
            except TypeError:
                serialisable.append({"module": entry.get("module"), "payload": {}})
        if not serialisable:
            return None
        return json.dumps(serialisable)


def _deserialise_actions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            value = None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return _normalise_actions(parsed)
    if isinstance(value, list):
        return _normalise_actions(value)
    if isinstance(value, Mapping):
        return _normalise_actions([value])
    return []


def _row_to_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_type": _coerce_text(row.get("event_type"), default=""),
        "display_name": _coerce_text(row.get("display_name"), default=""),
        "description": row.get("description"),
        "message_template": _coerce_text(row.get("message_template"), default=""),
        "is_user_visible": _coerce_bool(row.get("is_user_visible"), default=True),
        "allow_channel_in_app": _coerce_bool(row.get("allow_channel_in_app"), default=True),
        "allow_channel_email": _coerce_bool(row.get("allow_channel_email"), default=False),
        "allow_channel_sms": _coerce_bool(row.get("allow_channel_sms"), default=False),
        "default_channel_in_app": _coerce_bool(row.get("default_channel_in_app"), default=True),
        "default_channel_email": _coerce_bool(row.get("default_channel_email"), default=False),
        "default_channel_sms": _coerce_bool(row.get("default_channel_sms"), default=False),
        "module_actions": _deserialise_actions(row.get("module_actions")),
    }


async def list_settings() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            event_type,
            display_name,
            description,
            message_template,
            is_user_visible,
            allow_channel_in_app,
            allow_channel_email,
            allow_channel_sms,
            default_channel_in_app,
            default_channel_email,
            default_channel_sms,
            module_actions
        FROM notification_event_settings
        ORDER BY event_type
        """,
    )
    return [_row_to_dict(row) for row in rows]


async def get_setting(event_type: str) -> dict[str, Any] | None:
    cleaned = (event_type or "").strip()
    if not cleaned:
        return None
    row = await db.fetch_one(
        """
        SELECT
            event_type,
            display_name,
            description,
            message_template,
            is_user_visible,
            allow_channel_in_app,
            allow_channel_email,
            allow_channel_sms,
            default_channel_in_app,
            default_channel_email,
            default_channel_sms,
            module_actions
        FROM notification_event_settings
        WHERE event_type = %s
        LIMIT 1
        """,
        (cleaned,),
    )
    if not row:
        return None
    return _row_to_dict(row)


async def upsert_setting(event_type: str, values: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = (event_type or "").strip()
    if not cleaned:
        raise ValueError("Event type is required")

    display_name = _coerce_text(values.get("display_name"), default=cleaned).strip() or cleaned
    description = values.get("description")
    description_value = description if description is None else _coerce_text(description)
    message_template = _coerce_text(values.get("message_template"), default="{{ message }}").strip()
    is_user_visible = 1 if _coerce_bool(values.get("is_user_visible"), default=True) else 0
    allow_in_app = 1 if _coerce_bool(values.get("allow_channel_in_app"), default=True) else 0
    allow_email = 1 if _coerce_bool(values.get("allow_channel_email"), default=False) else 0
    allow_sms = 1 if _coerce_bool(values.get("allow_channel_sms"), default=False) else 0
    default_in_app = 1 if _coerce_bool(values.get("default_channel_in_app"), default=True) else 0
    default_email = 1 if _coerce_bool(values.get("default_channel_email"), default=False) else 0
    default_sms = 1 if _coerce_bool(values.get("default_channel_sms"), default=False) else 0
    actions_payload = _serialise_actions(values.get("module_actions"))

    params = (
        cleaned,
        display_name,
        description_value,
        message_template,
        is_user_visible,
        allow_in_app,
        allow_email,
        allow_sms,
        default_in_app,
        default_email,
        default_sms,
        actions_payload,
        display_name,
        description_value,
        message_template,
        is_user_visible,
        allow_in_app,
        allow_email,
        allow_sms,
        default_in_app,
        default_email,
        default_sms,
        actions_payload,
    )

    await db.execute(
        """
        INSERT INTO notification_event_settings (
            event_type,
            display_name,
            description,
            message_template,
            is_user_visible,
            allow_channel_in_app,
            allow_channel_email,
            allow_channel_sms,
            default_channel_in_app,
            default_channel_email,
            default_channel_sms,
            module_actions
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            display_name = %s,
            description = %s,
            message_template = %s,
            is_user_visible = %s,
            allow_channel_in_app = %s,
            allow_channel_email = %s,
            allow_channel_sms = %s,
            default_channel_in_app = %s,
            default_channel_email = %s,
            default_channel_sms = %s,
            module_actions = %s
        """,
        params,
    )

    refreshed = await get_setting(cleaned)
    if not refreshed:
        raise RuntimeError("Failed to persist notification event settings")
    return refreshed


async def delete_missing(event_types: Iterable[str]) -> None:
    identifiers = [str(item).strip() for item in event_types if str(item).strip()]
    if not identifiers:
        await db.execute("DELETE FROM notification_event_settings")
        return
    placeholders = ", ".join(["%s"] * len(identifiers))
    await db.execute(
        f"DELETE FROM notification_event_settings WHERE event_type NOT IN ({placeholders})",
        tuple(identifiers),
    )
