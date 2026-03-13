from __future__ import annotations

import json
from typing import Any

from app.core.database import db


def _normalise_menu_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    return key[:120]


def _coerce_preferences(payload: Any) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        return {"order": [], "hidden": []}

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

    return {"order": order, "hidden": hidden}


async def get_user_sidebar_preferences(user_id: int) -> dict[str, list[str]]:
    row = await db.fetch_one(
        """
        SELECT preferences_json
        FROM user_sidebar_preferences
        WHERE user_id = %s
        """,
        (user_id,),
    )
    if not row:
        return {"order": [], "hidden": []}

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
) -> dict[str, list[str]]:
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
