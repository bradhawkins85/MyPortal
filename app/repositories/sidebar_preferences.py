from __future__ import annotations

import json
from typing import Any

from app.core.database import db

PROTECTED_MENU_KEYS: set[str] = {"/admin/profile"}
ALLOWED_GROUP_ICONS: set[str] = {"folder", "shop", "briefcase", "grid", "star", "users"}


def _normalise_menu_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    return key[:120]


def _coerce_preferences(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"order": [], "hidden": [], "groups": []}

    order_values = payload.get("order") if isinstance(payload.get("order"), list) else []
    hidden_values = payload.get("hidden") if isinstance(payload.get("hidden"), list) else []

    order: list[str] = []
    hidden: list[str] = []
    seen_order: set[str] = set()
    seen_hidden: set[str] = set()

    for entry in order_values:
        key = _normalise_menu_key(entry)
        if key and key not in seen_order:
            seen_order.add(key)
            order.append(key)

    for entry in hidden_values:
        key = _normalise_menu_key(entry)
        if key and key not in seen_hidden:
            seen_hidden.add(key)
            hidden.append(key)

    hidden = [key for key in hidden if key not in PROTECTED_MENU_KEYS]

    groups: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    assigned_items: set[str] = set()
    raw_groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        group_id = _normalise_menu_key(raw_group.get("id"))
        if not group_id.startswith("__group__:") or group_id in seen_groups:
            continue
        label = str(raw_group.get("label") or "").strip()[:60]
        if not label:
            continue
        icon = str(raw_group.get("icon") or "folder").strip().lower()
        if icon not in ALLOWED_GROUP_ICONS:
            icon = "folder"
        items: list[str] = []
        raw_items = raw_group.get("items") if isinstance(raw_group.get("items"), list) else []
        for raw_item in raw_items:
            item = _normalise_menu_key(raw_item)
            if (
                item
                and item not in assigned_items
                and item not in PROTECTED_MENU_KEYS
                and not item.startswith("__group__:")
                and not item.startswith("__divider__:")
                and not item.startswith("__spacer__:")
            ):
                items.append(item)
                assigned_items.add(item)
        seen_groups.add(group_id)
        groups.append({"id": group_id, "label": label, "icon": icon, "items": items})

    # Group markers are useful in the order even if a client omitted them.
    for group in groups:
        if group["id"] not in seen_order:
            order.append(group["id"])

    return {"order": order, "hidden": hidden, "groups": groups}


async def get_user_sidebar_preferences(user_id: int) -> dict[str, Any]:
    row = await db.fetch_one(
        """
        SELECT preferences_json
        FROM user_sidebar_preferences
        WHERE user_id = %s
        """,
        (user_id,),
    )
    if not row:
        return {"order": [], "hidden": [], "groups": []}

    raw_preferences = row.get("preferences_json")
    parsed: Any
    if isinstance(raw_preferences, str):
        try:
            parsed = json.loads(raw_preferences)
        except json.JSONDecodeError:
            parsed = {}
    else:
        parsed = raw_preferences

    return _coerce_preferences(parsed)


async def upsert_user_sidebar_preferences(
    user_id: int,
    preferences: dict[str, Any],
) -> dict[str, Any]:
    safe_preferences = _coerce_preferences(preferences)
    payload = json.dumps(safe_preferences)

    await db.execute(
        """
        INSERT INTO user_sidebar_preferences (user_id, preferences_json)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            preferences_json = VALUES(preferences_json),
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, payload),
    )

    return safe_preferences
