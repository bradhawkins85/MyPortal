"""Service for resolving and validating tray ticket dynamic questions.

Responsibilities
----------------

* Fetch the effective ordered question list for a device/company by merging
  global questions and company-scoped questions (global first, then company).
* Evaluate conditional visibility: a question is *visible* only when all of
  its conditions are satisfied given the current set of answers.
* Validate a submitted answer set: required visible questions must have a
  non-empty value; select questions must use a declared option.
* Build the "Additional Details" section appended to the ticket description.
"""

from __future__ import annotations

from typing import Any

from app.repositories import tray_ticket_questions as q_repo


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


async def get_questions_for_company(
    company_id: int | None,
) -> list[dict[str, Any]]:
    """Return the merged, ordered question list for a company.

    Global questions come first (ordered by sort_order), followed by
    company-scoped questions (ordered by sort_order).  Each returned dict
    includes a ``conditions`` key with a (possibly empty) list of condition
    dicts.
    """
    global_qs = await q_repo.list_questions(scope="global", active_only=True)
    company_qs: list[dict[str, Any]] = []
    if company_id is not None:
        company_qs = await q_repo.list_questions(
            scope="company", company_id=company_id, active_only=True
        )

    all_qs = global_qs + company_qs
    if not all_qs:
        return []

    all_ids = [int(q["id"]) for q in all_qs]
    raw_conditions = await q_repo.list_conditions_for_questions(all_ids)

    # Index conditions by question_id for O(1) lookup.
    cond_index: dict[int, list[dict[str, Any]]] = {}
    for cond in raw_conditions:
        qid = int(cond["question_id"])
        cond_index.setdefault(qid, []).append(dict(cond))

    for q in all_qs:
        q["conditions"] = cond_index.get(int(q["id"]), [])

    return all_qs


def evaluate_visibility(
    question: dict[str, Any],
    answers_by_qid: dict[int, str],
) -> bool:
    """Return True when *all* conditions on ``question`` are satisfied.

    If the question has no conditions it is always visible.

    Operators:
    * ``equals``     — answer value equals expected_value (case-insensitive)
    * ``not_equals`` — answer value does not equal expected_value
    * ``contains``   — answer value contains expected_value substring
    """
    conditions = question.get("conditions") or []
    for cond in conditions:
        parent_id = int(cond["parent_question_id"])
        operator = str(cond.get("operator", "equals")).lower()
        expected = str(cond.get("expected_value", "")).lower()
        actual = str(answers_by_qid.get(parent_id, "")).lower()

        if operator == "equals":
            if actual != expected:
                return False
        elif operator == "not_equals":
            if actual == expected:
                return False
        elif operator == "contains":
            if expected not in actual:
                return False
        # Unknown operators default to invisible to be conservative.
        else:
            return False

    return True


def get_visible_questions(
    questions: list[dict[str, Any]],
    answers_by_qid: dict[int, str],
) -> list[dict[str, Any]]:
    """Return the subset of questions that are currently visible.

    Visibility is evaluated in order so that a later question can depend on the
    answer to an earlier one (questions must be sorted before being passed in).
    """
    visible: list[dict[str, Any]] = []
    # Resolve incrementally: a newly visible question's answer may unlock more.
    for q in questions:
        if evaluate_visibility(q, answers_by_qid):
            visible.append(q)
    return visible


def validate_answers(
    questions: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
) -> list[str]:
    """Validate submitted answers against visible required questions.

    ``submitted`` is a list of dicts with keys ``question_id`` and ``value``.

    Returns a list of error messages.  An empty list means the answers are
    valid.
    """
    answers_by_qid: dict[int, str] = {
        int(a["question_id"]): str(a.get("value") or "").strip()
        for a in submitted
        if a.get("question_id") is not None
    }

    visible = get_visible_questions(questions, answers_by_qid)
    errors: list[str] = []

    for q in visible:
        qid = int(q["id"])
        value = answers_by_qid.get(qid, "").strip()
        label = q.get("label", f"Question {qid}")

        if q.get("is_required") and not value:
            errors.append(f"'{label}' is required.")
            continue

        if q.get("field_type") == "select" and value:
            options = [str(o) for o in (q.get("options") or [])]
            if options and value not in options:
                errors.append(f"'{label}' must be one of: {', '.join(options)}.")

    return errors


def build_additional_details(
    questions: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
) -> str:
    """Build a Markdown block of additional details for the ticket body.

    Only includes answers to visible questions that have a non-empty value.
    Returns an empty string when there is nothing to append.
    """
    answers_by_qid: dict[int, str] = {
        int(a["question_id"]): str(a.get("value") or "").strip()
        for a in submitted
        if a.get("question_id") is not None
    }

    visible = get_visible_questions(questions, answers_by_qid)
    lines: list[str] = []

    for q in visible:
        qid = int(q["id"])
        value = answers_by_qid.get(qid, "").strip()
        if value:
            label = q.get("label", f"Question {qid}")
            lines.append(f"**{label}:** {value}")

    if not lines:
        return ""

    return "**Additional Details**\n\n" + "\n\n".join(lines)


def build_answer_snapshots(
    questions: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a list of answer snapshot dicts ready for persistence.

    Each snapshot has:
    * ``question_id``              — the definition id (int or None)
    * ``question_label_snapshot``  — label string at submission time
    * ``is_required_snapshot``     — whether the question was required at
                                     submission time (bool)
    * ``answer_value``             — the submitted string value (may be None)
    """
    answers_by_qid: dict[int, str] = {
        int(a["question_id"]): str(a.get("value") or "").strip()
        for a in submitted
        if a.get("question_id") is not None
    }

    visible = get_visible_questions(questions, answers_by_qid)
    question_index = {int(q["id"]): q for q in visible}

    snapshots: list[dict[str, Any]] = []
    for a in submitted:
        if a.get("question_id") is None:
            continue
        qid = int(a["question_id"])
        q = question_index.get(qid)
        if q is None:
            # Question not visible — skip (do not persist hidden answers)
            continue
        value = str(a.get("value") or "").strip() or None
        snapshots.append(
            {
                "question_id": qid,
                "question_label_snapshot": q.get("label", f"Question {qid}"),
                "is_required_snapshot": bool(q.get("is_required")),
                "answer_value": value,
            }
        )

    return snapshots
