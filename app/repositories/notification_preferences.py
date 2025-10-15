from __future__ import annotations

from typing import Any, Iterable

from app.core.database import db


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        value_lower = value.strip().lower()
        if value_lower in {"1", "true", "yes", "on"}:
            return True
        if value_lower in {"0", "false", "no", "off"}:
            return False
    return default


async def list_preferences(user_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT event_type, channel_in_app, channel_email, channel_sms
        FROM notification_preferences
        WHERE user_id = %s
        ORDER BY event_type
        """,
        (user_id,),
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "event_type": row.get("event_type", ""),
                "channel_in_app": _coerce_bool(row.get("channel_in_app"), default=True),
                "channel_email": _coerce_bool(row.get("channel_email"), default=False),
                "channel_sms": _coerce_bool(row.get("channel_sms"), default=False),
            }
        )
    return results


async def get_preference(user_id: int, event_type: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT event_type, channel_in_app, channel_email, channel_sms
        FROM notification_preferences
        WHERE user_id = %s AND event_type = %s
        """,
        (user_id, event_type),
    )
    if not row:
        return None
    return {
        "event_type": row.get("event_type", event_type),
        "channel_in_app": _coerce_bool(row.get("channel_in_app"), default=True),
        "channel_email": _coerce_bool(row.get("channel_email"), default=False),
        "channel_sms": _coerce_bool(row.get("channel_sms"), default=False),
    }


async def upsert_preferences(user_id: int, preferences: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[tuple[Any, ...]] = []
    normalised_event_types: list[str] = []
    for preference in preferences:
        event_type = (preference.get("event_type") or "").strip()
        if not event_type:
            continue
        in_app = 1 if _coerce_bool(preference.get("channel_in_app"), default=True) else 0
        email = 1 if _coerce_bool(preference.get("channel_email"), default=False) else 0
        sms = 1 if _coerce_bool(preference.get("channel_sms"), default=False) else 0
        prepared.append((user_id, event_type, in_app, email, sms))
        normalised_event_types.append(event_type)

    if prepared:
        values = ", ".join(["(%s, %s, %s, %s, %s)"] * len(prepared))
        params: list[Any] = []
        for row in prepared:
            params.extend(row)
        await db.execute(
            f"""
            INSERT INTO notification_preferences (user_id, event_type, channel_in_app, channel_email, channel_sms)
            VALUES {values}
            ON DUPLICATE KEY UPDATE
                channel_in_app = VALUES(channel_in_app),
                channel_email = VALUES(channel_email),
                channel_sms = VALUES(channel_sms)
            """,
            tuple(params),
        )
    else:
        await db.execute("DELETE FROM notification_preferences WHERE user_id = %s", (user_id,))
        return []

    if normalised_event_types:
        placeholders = ", ".join(["%s"] * len(normalised_event_types))
        await db.execute(
            f"""
            DELETE FROM notification_preferences
            WHERE user_id = %s AND event_type NOT IN ({placeholders})
            """,
            tuple([user_id] + normalised_event_types),
        )
    else:
        await db.execute("DELETE FROM notification_preferences WHERE user_id = %s", (user_id,))

    return await list_preferences(user_id)
