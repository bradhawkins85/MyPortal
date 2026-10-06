"""Core API route for in-app user feedback.

Exposes ``POST /api/feedback`` which records a 👍/👎 feedback submission from the
feedback widget in the top menu bar, creates a follow-up ticket for the support
team, persists the feedback row, and records an audit event.

This is a plain core API route (registered in ``app/main.py``) rather than a
feature-pack route because it is a single, self-contained JSON endpoint with no
template surface.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.core.logging import log_error
from app.repositories import user_feedback as user_feedback_repo
from app.services import audit as audit_service
from app.services import tickets as tickets_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["api-feedback"])


class FeedbackSubmit(BaseModel):
    """Request body for ``POST /api/feedback``."""

    rating: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=2000)
    suggested_improvements: str | None = Field(default=None, max_length=4000)
    page_url: str | None = Field(default=None, max_length=2048)


def _clean(value: str | None) -> str | None:
    """Trim a user-supplied string; return ``None`` when it is blank."""
    if value is None:
        return None
    text = value.strip()
    return text or None


@router.post("", status_code=201)
async def submit_feedback(
    payload: FeedbackSubmit,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db=Depends(require_database),
) -> JSONResponse:
    """Record feedback and open a follow-up ticket for the support team."""
    rating = payload.rating
    reason = _clean(payload.reason)
    suggested = _clean(payload.suggested_improvements)
    page_url = _clean(payload.page_url)

    # Negative feedback should carry a reason or suggestion so the team can act.
    if rating == "down" and not (reason or suggested):
        raise HTTPException(
            status_code=422,
            detail="Please add a reason or a suggested improvement.",
        )

    user_id = current_user.get("id")
    company_id = current_user.get("company_id")
    try:
        company_id = int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        company_id = None

    rating_label = "Positive (thumbs up)" if rating == "up" else "Negative (thumbs down)"
    page_hint = f" (page: {page_url})" if page_url else ""
    subject = f"User feedback — {rating_label}{page_hint}"[:255]

    description_lines = [f"Rating: {rating_label}"]
    if page_url:
        description_lines.append(f"Page: {page_url}")
    if reason:
        description_lines.append(f"Reason: {reason}")
    if suggested:
        description_lines.append(f"Suggested improvements: {suggested}")
    description_lines.append("\n---\nSubmitted from the in-app feedback widget.")
    description = "\n".join(description_lines)

    try:
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=description,
            requester_id=user_id,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status=await tickets_service.resolve_status_or_default(None),
            category=None,
            module_slug=None,
            external_reference=None,
            initial_reply_author_id=user_id,
            trigger_automations=False,
            send_creation_notification=False,
            record_initial_reply=True,
        )
    except Exception as exc:  # noqa: BLE001 - surface a friendly error to the widget
        log_error(
            "feedback: failed to create follow-up ticket",
            exc=exc,
            user_id=user_id,
            rating=rating,
        )
        raise HTTPException(
            status_code=500,
            detail="We couldn't create a ticket for your feedback. Please try again.",
        ) from exc

    ticket_id = ticket.get("id")

    # Persist the feedback row (best-effort; the ticket already exists above).
    feedback_id: int | None = None
    try:
        feedback_id = await user_feedback_repo.create_feedback(
            user_id=user_id,
            ticket_id=ticket_id,
            rating=rating,
            reason=reason,
            suggested_improvements=suggested,
            page_url=page_url,
        )
    except Exception as exc:  # noqa: BLE001
        log_error(
            "feedback: failed to persist feedback row",
            exc=exc,
            user_id=user_id,
            ticket_id=ticket_id,
        )

    await audit_service.record(
        action="feedback.submit",
        request=request,
        user_id=user_id,
        entity_type="ticket",
        entity_id=ticket_id,
        metadata={
            "rating": rating,
            "page_url": page_url,
            "user_feedback_id": feedback_id,
        },
    )

    return JSONResponse(
        {"ok": True, "ticket_id": ticket_id, "user_feedback_id": feedback_id},
        status_code=201,
    )