"""Repository for generic per-user UI preferences (e.g. table column visibility).

Stored in the ``user_preferences`` table introduced in migration 208.
Each preference is a (user_id, preference_key) pair carrying an arbitrary
JSON document. Keys follow the convention ``<area>:<id>:<aspect>`` — for
example ``tables:tickets-history:columns``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.database import db

# Conservative key validation. Keep in sync with the documentation in
# docs/ui_layout_standards.md and the JS in app/static/js/table_columns.js.
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.\-]{0,189}$")
MAX_VALUE_BYTES = 16 * 1024  # 16 KiB; UI preference payloads are tiny.


class InvalidPreferenceKey(ValueError):
    """Raised when a caller-supplied key fails validation."""


class InvalidPreferenceValue(ValueError):
    """Raised when a caller-supplied value cannot be safely persisted."""


def validate_key(key: Any) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise InvalidPreferenceKey("Invalid preference key")
    return key


def _serialise_value(value: Any) -> str:
    try:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise InvalidPreferenceValue("Preference value must be JSON-serialisable") from exc
    if len(payload.encode("utf-8")) > MAX_VALUE_BYTES:
        raise InvalidPreferenceValue("Preference value exceeds maximum size")
    return payload


def _deserialise_value(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


async def get_preference(user_id: int, key: str) -> Any:
    """Return the parsed JSON value for ``key`` or ``None`` if not set."""
    validate_key(key)
    row = await db.fetch_one(
        """
        SELECT preference_value
        FROM user_preferences
        WHERE user_id = ? AND preference_key = ?
        """,
        (user_id, key),
    )
    if not row:
        return None
    return _deserialise_value(row.get("preference_value"))


async def set_preference(user_id: int, key: str, value: Any) -> Any:
    """Upsert ``key`` for ``user_id``. Returns the value as stored."""
    validate_key(key)
    payload = _serialise_value(value)
    if db.is_sqlite():
        await db.execute(
            """
            INSERT INTO user_preferences (user_id, preference_key, preference_value)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, preference_key) DO UPDATE SET
                preference_value = excluded.preference_value,
                updated_at = datetime('now')
            """,
            (user_id, key, payload),
        )
    else:
        await db.execute(
            """
            INSERT INTO user_preferences (user_id, preference_key, preference_value)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                preference_value = VALUES(preference_value),
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, key, payload),
        )
    return value


async def delete_preference(user_id: int, key: str) -> None:
    validate_key(key)
    await db.execute(
        "DELETE FROM user_preferences WHERE user_id = ? AND preference_key = ?",
        (user_id, key),
    )
