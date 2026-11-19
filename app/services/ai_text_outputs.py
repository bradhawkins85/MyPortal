"""Record AI text shown on a page so people can rate it 👍/👎."""
from __future__ import annotations

from typing import Any, Mapping

from app.core.logging import log_warning
from app.repositories import ai_quality as quality_repo


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


async def ticket_rating_ids(ticket: Mapping[str, Any], *, viewer_id: int | None) -> dict[str, Any]:
    """Output ids (and the viewer's current vote) for a ticket's AI summary and AI resolution steps.

    Outputs are recorded when shown; the same text is recorded once. Steps a
    technician edited are not AI output. Any failure hides the controls.
    """
    ids: dict[str, Any] = {"summary": None, "summary_rating": None,
                           "resolution_steps": None, "resolution_steps_rating": None}
    try:
        common = {"user_id": None, "company_id": ticket.get("company_id"), "subject_type": "ticket",
                  "subject_id": ticket.get("id")}
        if ticket.get("ai_summary_status") == "succeeded" and _has_text(ticket.get("ai_summary")):
            ids["summary"] = await quality_repo.record_output(
                feature="ticket_ai_summary", **common, output_text=str(ticket["ai_summary"]),
                model=ticket.get("ai_summary_model"),
            )
        if ticket.get("resolution_steps_source") == "ai_generated" and _has_text(ticket.get("resolution_steps")):
            ids["resolution_steps"] = await quality_repo.record_output(
                feature="ticket_resolution_steps", **common, output_text=str(ticket["resolution_steps"]),
                model=ticket.get("resolution_steps_model"),
            )
        if viewer_id is not None:
            for key in ("summary", "resolution_steps"):
                if ids[key]:
                    ids[key + "_rating"] = await quality_repo.user_rating(ids[key], int(viewer_id))
    except Exception as exc:
        log_warning("Could not record ticket AI output for rating", ticket_id=ticket.get("id"), error=str(exc))
        return {"summary": None, "summary_rating": None, "resolution_steps": None, "resolution_steps_rating": None}
    return ids
