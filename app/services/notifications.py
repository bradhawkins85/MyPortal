from __future__ import annotations

from html import escape
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.repositories import notification_preferences as preferences_repo
from app.repositories import notifications as notifications_repo
from app.repositories import users as user_repo
from app.services import email as email_service


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
        if user_id is None:
            logger.warning(
                "Email delivery requested for notification but no user_id provided",
                event_type=event_type,
            )
        else:
            user = await user_repo.get_user_by_id(user_id)
            if not user or not user.get("email"):
                logger.warning(
                    "Email delivery requested for notification but user has no email",
                    user_id=user_id,
                    event_type=event_type,
                )
            else:
                subject = f"{get_settings().app_name} notification: {event_type}"
                text_body = message
                html_body = f"<p>{escape(message)}</p>"
                try:
                    sent = await email_service.send_email(
                        subject=subject,
                        recipients=[user["email"]],
                        text_body=text_body,
                        html_body=html_body,
                    )
                    if not sent:
                        logger.warning(
                            "Notification email delivery skipped because SMTP is not configured",
                            user_id=user_id,
                            event_type=event_type,
                        )
                except email_service.EmailDispatchError as exc:  # pragma: no cover - log for visibility
                    logger.error(
                        "Failed to send notification email",
                        user_id=user_id,
                        event_type=event_type,
                        error=str(exc),
                    )

    if preference and preference.get("channel_sms"):
        logger.debug(
            "SMS delivery requested for notification", user_id=user_id, event_type=event_type
        )
