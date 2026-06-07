"""Repository for tray ticket dynamic questions and submitted answers.

All access to ``tray_ticket_questions``, ``tray_ticket_question_conditions``
and ``tray_ticket_answers`` flows through this module.  Functions accept and
return plain ``dict`` objects, matching the style used by other repositories
such as :mod:`app.repositories.tray`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ph() -> str:
    """Return the appropriate SQL placeholder for the current DB backend."""
    return "?" if db.is_sqlite() else "%s"


def _decode_question(row: dict[str, Any]) -> dict[str, Any]:
    """Decode ``options_json`` in-place and return the row."""
    raw = row.get("options_json")
    if raw:
        try:
            row["options"] = json.loads(raw)
        except (ValueError, TypeError):
            row["options"] = []
    else:
        row["options"] = []
    return row


# ---------------------------------------------------------------------------
# Question definitions
# ---------------------------------------------------------------------------


async def list_questions(
    *,
    scope: str | None = None,
    company_id: int | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """Return question definitions ordered by sort_order then id.

    Pass ``scope='global'`` for global questions only, ``scope='company'``
    with ``company_id`` for company-scoped questions, or omit both to return
    all questions.
    """
    p = _ph()
    clauses: list[str] = []
    params: list[Any] = []

    if scope is not None:
        clauses.append(f"scope = {p}")
        params.append(scope)
    if company_id is not None:
        clauses.append(f"company_id = {p}")
        params.append(company_id)
    if active_only:
        clauses.append("is_active = 1")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await db.fetch_all(
        f"SELECT * FROM tray_ticket_questions {where} ORDER BY sort_order ASC, id ASC",
        tuple(params) if params else (),
    )
    return [_decode_question(dict(r)) for r in rows]


async def get_question(question_id: int) -> dict[str, Any] | None:
    p = _ph()
    row = await db.fetch_one(
        f"SELECT * FROM tray_ticket_questions WHERE id = {p}",
        (question_id,),
    )
    return _decode_question(dict(row)) if row else None


async def create_question(
    *,
    scope: str,
    company_id: int | None,
    field_type: str,
    label: str,
    placeholder: str | None,
    is_required: bool,
    options: list[str],
    sort_order: int,
    is_active: bool,
    created_by_user_id: int | None,
) -> dict[str, Any]:
    p = _ph()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    options_json = json.dumps(options) if options else None
    await db.execute(
        f"INSERT INTO tray_ticket_questions "
        f"(scope, company_id, field_type, label, placeholder, is_required, "
        f"options_json, sort_order, is_active, created_by_user_id, created_at, updated_at) "
        f"VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})",
        (
            scope,
            company_id,
            field_type,
            label,
            placeholder,
            1 if is_required else 0,
            options_json,
            sort_order,
            1 if is_active else 0,
            created_by_user_id,
            now,
            now,
        ),
    )
    row = await db.fetch_one(
        "SELECT * FROM tray_ticket_questions ORDER BY id DESC LIMIT 1"
    )
    return _decode_question(dict(row)) if row else {}


async def update_question(
    question_id: int,
    *,
    field_type: str | None = None,
    label: str | None = None,
    placeholder: str | None = None,
    is_required: bool | None = None,
    options: list[str] | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
) -> dict[str, Any] | None:
    p = _ph()
    sets: list[str] = []
    params: list[Any] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if field_type is not None:
        sets.append(f"field_type = {p}")
        params.append(field_type)
    if label is not None:
        sets.append(f"label = {p}")
        params.append(label)
    if placeholder is not None:
        sets.append(f"placeholder = {p}")
        params.append(placeholder)
    if is_required is not None:
        sets.append(f"is_required = {p}")
        params.append(1 if is_required else 0)
    if options is not None:
        sets.append(f"options_json = {p}")
        params.append(json.dumps(options) if options else None)
    if sort_order is not None:
        sets.append(f"sort_order = {p}")
        params.append(sort_order)
    if is_active is not None:
        sets.append(f"is_active = {p}")
        params.append(1 if is_active else 0)

    if not sets:
        return await get_question(question_id)

    sets.append(f"updated_at = {p}")
    params.append(now)
    params.append(question_id)

    await db.execute(
        f"UPDATE tray_ticket_questions SET {', '.join(sets)} WHERE id = {p}",
        tuple(params),
    )
    return await get_question(question_id)


async def delete_question(question_id: int) -> None:
    p = _ph()
    await db.execute(
        f"DELETE FROM tray_ticket_questions WHERE id = {p}",
        (question_id,),
    )


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


async def list_conditions_for_question(question_id: int) -> list[dict[str, Any]]:
    p = _ph()
    rows = await db.fetch_all(
        f"SELECT * FROM tray_ticket_question_conditions WHERE question_id = {p}",
        (question_id,),
    )
    return [dict(r) for r in rows]


async def list_conditions_for_questions(
    question_ids: list[int],
) -> list[dict[str, Any]]:
    """Bulk-fetch conditions for a set of question IDs in one query."""
    if not question_ids:
        return []
    placeholders = ", ".join([_ph()] * len(question_ids))
    rows = await db.fetch_all(
        f"SELECT * FROM tray_ticket_question_conditions "
        f"WHERE question_id IN ({placeholders})",
        tuple(question_ids),
    )
    return [dict(r) for r in rows]


async def replace_conditions_for_question(
    question_id: int,
    conditions: list[dict[str, Any]],
) -> None:
    """Replace all conditions for ``question_id`` atomically."""
    p = _ph()
    await db.execute(
        f"DELETE FROM tray_ticket_question_conditions WHERE question_id = {p}",
        (question_id,),
    )
    for cond in conditions:
        await db.execute(
            f"INSERT INTO tray_ticket_question_conditions "
            f"(question_id, parent_question_id, operator, expected_value) "
            f"VALUES ({p},{p},{p},{p})",
            (
                question_id,
                int(cond["parent_question_id"]),
                str(cond.get("operator", "equals")),
                str(cond.get("expected_value", "")),
            ),
        )


# ---------------------------------------------------------------------------
# Submitted answers
# ---------------------------------------------------------------------------


async def create_answers(
    ticket_id: int,
    answers: list[dict[str, Any]],
) -> None:
    """Persist a list of answer snapshots for a newly created ticket.

    Each entry must have ``question_label_snapshot``, ``is_required_snapshot``,
    and ``answer_value``; the ``question_id`` key is optional (it may be None
    when the question definition has been deleted between loading and submitting
    the form).
    """
    p = _ph()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for ans in answers:
        await db.execute(
            f"INSERT INTO tray_ticket_answers "
            f"(ticket_id, question_id, question_label_snapshot, is_required_snapshot, answer_value, created_at) "
            f"VALUES ({p},{p},{p},{p},{p},{p})",
            (
                ticket_id,
                ans.get("question_id"),
                ans["question_label_snapshot"],
                1 if ans.get("is_required_snapshot") else 0,
                ans.get("answer_value"),
                now,
            ),
        )


async def list_answers_for_ticket(ticket_id: int) -> list[dict[str, Any]]:
    p = _ph()
    rows = await db.fetch_all(
        f"SELECT * FROM tray_ticket_answers WHERE ticket_id = {p} ORDER BY id ASC",
        (ticket_id,),
    )
    return [dict(r) for r in rows]
