from __future__ import annotations

from typing import Any

from loguru import logger

from app.repositories import notification_preferences as preferences_repo
from app.repositories import notifications as notifications_repo


async def emit_notification(
    *,
    event_type: str,
    message: str,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Create a notification record for the supplied event."""

    should_record = True
    preference: dict[str, Any] | None = None

    if user_id is not None:
        try:
            preference = await preferences_repo.get_preference(user_id, event_type)
        except Exception as exc:  # pragma: no cover - safety net for unexpected DB issues
            logger.warning(
                "Failed to load notification preference", user_id=user_id, event_type=event_type, error=str(exc)
            )
            preference = None
        if preference and not preference.get("channel_in_app", True):
            should_record = False

    if should_record:
        await notifications_repo.create_notification(
            event_type=event_type,
            message=message,
            user_id=user_id,
            metadata=metadata or {},
        )

    if preference and preference.get("channel_email"):
        logger.debug(
            "Email delivery requested for notification", user_id=user_id, event_type=event_type
        )

    if preference and preference.get("channel_sms"):
        logger.debug(
            "SMS delivery requested for notification", user_id=user_id, event_type=event_type
        )
