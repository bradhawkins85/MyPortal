"""Global custom question definitions for every client onboarding form."""

from __future__ import annotations

import json
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT id, label, field_type, section, help_text, required, options_json, "
    "display_order FROM client_onboarding_questions"
)


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    record["id"] = int(record["id"])
    record["display_order"] = int(record.get("display_order") or 0)
    record["required"] = str(record.get("required") or "").lower() in {"1", "true"}
    raw_options = record.pop("options_json", None)
    try:
        options = json.loads(raw_options) if isinstance(raw_options, str) else raw_options
    except (TypeError, ValueError):
        options = []
    record["options"] = options if isinstance(options, list) and all(isinstance(option, str) for option in options) else []
    record["help_text"] = str(record.get("help_text") or "")
    return record


async def get_question(question_id: int) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(_SELECT + " WHERE id = ?", (int(question_id),)))


async def list_questions() -> list[dict[str, Any]]:
    rows = await db.fetch_all(_SELECT + " ORDER BY display_order, id")
    return [record for record in (_normalise(row) for row in rows) if record]


async def save_question(question_id: int | None, data: dict[str, Any]) -> dict[str, Any] | None:
    values = (
        data["label"], data["field_type"], data["section"], data.get("help_text") or None,
        int(bool(data.get("required"))), json.dumps(data.get("options") or [], ensure_ascii=False),
        int(data.get("display_order") or 0),
    )
    if question_id is None:
        question_id = int(await db.execute_returning_lastrowid(
            """INSERT INTO client_onboarding_questions
               (label, field_type, section, help_text, required, options_json, display_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", values,
        ))
    else:
        await db.execute(
            """UPDATE client_onboarding_questions
               SET label = ?, field_type = ?, section = ?, help_text = ?,
                   required = ?, options_json = ?, display_order = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""", (*values, int(question_id)),
        )
    return await get_question(question_id)


async def delete_question(question_id: int) -> bool:
    return await db.execute_rowcount(
        "DELETE FROM client_onboarding_questions WHERE id = ?", (int(question_id),),
    ) == 1
