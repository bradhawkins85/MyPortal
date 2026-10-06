"""Repository for user feedback submitted through the in-app feedback widget."""
from __future__ import annotations

from typing import Any

from app.core.database import db


async def create_feedback(
    user_id: int,
    ticket_id: int | None,
    rating: str,
    reason: str | None,
    suggested_improvements: str | None,
    page_url: str | None,
) -> int:
    """Insert a feedback row and return the new id.

    ``ticket_id`` is the follow-up ticket created for the support team. It may be
    ``None`` if ticket creation failed but the feedback should still be recorded.
    """
    return await db.execute_returning_lastrowid(
        """INSERT INTO user_feedback
           (user_id, ticket_id, rating, reason, suggested_improvements, page_url)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, ticket_id, rating, reason, suggested_improvements, page_url),
    )


async def get_feedback(feedback_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """SELECT id, user_id, ticket_id, rating, reason,
                  suggested_improvements, page_url, created_at
           FROM user_feedback
           WHERE id = %s""",
        (feedback_id,),
    )
    return row or None