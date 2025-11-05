from __future__ import annotations

from html import escape
from typing import Any, Mapping

from loguru import logger

from app.core.config import get_settings
from app.repositories import notification_preferences as preferences_repo
from app.repositories import notifications as notifications_repo
from app.repositories import users as user_repo
from app.services import email as email_service
from app.services import modules as modules_service
from app.services import notification_event_settings
from app.services import sms as sms_service
from app.services import value_templates


async def emit_notification(
    *,
    event_type: str,
    message: str | None = None,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Create a notification record for the supplied event."""

    metadata_payload = metadata or {}
    try:
        event_setting = await notification_event_settings.get_event_setting(event_type)
    except Exception as exc:  # pragma: no cover - defensive default
        logger.warning(
            "Failed to load notification event configuration",
            event_type=event_type,
            error=str(exc),
        )
        event_setting = {
            "event_type": event_type,
            "display_name": event_type,
            "message_template": "{{ message }}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": False,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": False,
            "default_channel_sms": False,
        }

    allow_in_app = bool(event_setting.get("allow_channel_in_app", True))
    allow_email_value = event_setting.get("allow_channel_email")
    allow_email = True if allow_email_value is None else bool(allow_email_value)
    allow_sms_value = event_setting.get("allow_channel_sms")
    allow_sms = True if allow_sms_value is None else bool(allow_sms_value)
    default_in_app = bool(event_setting.get("default_channel_in_app", True))
    default_email = bool(event_setting.get("default_channel_email", False))
    default_sms = bool(event_setting.get("default_channel_sms", False))

    preference: dict[str, Any] | None = None
    channels = {
        "channel_in_app": allow_in_app and default_in_app,
        "channel_email": allow_email and default_email,
        "channel_sms": allow_sms and default_sms,
    }

    if user_id is not None:
        try:
            preference = await preferences_repo.get_preference(user_id, event_type)
        except Exception as exc:  # pragma: no cover - safety net for unexpected DB issues
            logger.warning(
                "Failed to load notification preference", user_id=user_id, event_type=event_type, error=str(exc)
            )
            preference = None
        if preference:
            channels["channel_in_app"] = bool(preference.get("channel_in_app")) and allow_in_app
            channels["channel_email"] = bool(preference.get("channel_email")) and allow_email
            channels["channel_sms"] = bool(preference.get("channel_sms")) and allow_sms
        else:
            channels["channel_in_app"] = allow_in_app and default_in_app
            channels["channel_email"] = allow_email and default_email
            channels["channel_sms"] = allow_sms and default_sms
    else:
        channels["channel_in_app"] = allow_in_app
        channels["channel_email"] = False
        channels["channel_sms"] = False

    should_record = allow_in_app and channels["channel_in_app"]

    context: dict[str, Any] = {
        "event_type": event_type,
        "metadata": metadata_payload,
        "message": message,
        "channels": dict(channels),
    }
    if user_id is not None:
        context["user_id"] = user_id
    
    # Expose ticket data directly in context if present in metadata
    # This allows {{ticket.number}} to work in notification templates
    if isinstance(metadata_payload, Mapping) and "ticket" in metadata_payload:
        context["ticket"] = metadata_payload["ticket"]

    template = str(event_setting.get("message_template") or "{{ message }}")
    rendered_message: str | None = None
    try:
        rendered = await value_templates.render_string_async(template, context)
    except Exception as exc:  # pragma: no cover - template guard
        logger.error(
            "Failed to render notification message template",
            event_type=event_type,
            error=str(exc),
        )
        rendered = None

    if isinstance(rendered, (dict, list)):
        rendered_message = await value_templates.render_value_async(rendered, context)
        rendered_message = str(rendered_message)
    elif rendered is not None:
        rendered_message = str(rendered)

    final_message = (rendered_message or message or event_setting.get("display_name") or event_type)
    context["message"] = final_message

    if should_record:
        await notifications_repo.create_notification(
            event_type=event_type,
            message=final_message,
            user_id=user_id,
            metadata=metadata_payload,
        )

    user: dict[str, Any] | None = None
    if user_id is not None and (channels["channel_email"] or channels["channel_sms"]):
        try:
            user = await user_repo.get_user_by_id(user_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to load user for notification delivery",
                user_id=user_id,
                event_type=event_type,
                error=str(exc),
            )
            user = None

    if channels["channel_email"]:
        if user_id is None:
            logger.warning(
                "Email delivery requested for notification but no user_id provided",
                event_type=event_type,
            )
        elif not user:
            logger.warning(
                "Email delivery requested for notification but user record was unavailable",
                user_id=user_id,
                event_type=event_type,
            )
        elif not user.get("email"):
            logger.warning(
                "Email delivery requested for notification but user has no email",
                user_id=user_id,
                event_type=event_type,
            )
        else:
            subject = f"{get_settings().app_name} notification: {event_type}"
            text_body = final_message
            html_body = f"<p>{escape(final_message)}</p>"
            try:
                sent, event_metadata = await email_service.send_email(
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
                        event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
                    )
            except email_service.EmailDispatchError as exc:  # pragma: no cover - log for visibility
                logger.error(
                    "Failed to send notification email",
                    user_id=user_id,
                    event_type=event_type,
                    error=str(exc),
                )

    if channels["channel_sms"]:
        if user_id is None:
            logger.warning(
                "SMS delivery requested for notification but no user_id provided",
                event_type=event_type,
            )
            return

        if not user:
            logger.warning(
                "SMS delivery requested for notification but user record was unavailable",
                user_id=user_id,
                event_type=event_type,
            )
            return

        phone_number = (user.get("mobile_phone") or "").strip()
        if not phone_number:
            logger.warning(
                "SMS delivery requested for notification but user has no mobile phone",
                user_id=user_id,
                event_type=event_type,
            )
            return

        try:
            sent = await sms_service.send_sms(message=final_message, phone_numbers=[phone_number])
        except sms_service.SMSDispatchError as exc:  # pragma: no cover - log for visibility
            logger.error(
                "Failed to send notification SMS",
                user_id=user_id,
                event_type=event_type,
                error=str(exc),
            )
            return

        if not sent:
            logger.warning(
                "Notification SMS delivery skipped because the gateway is not configured",
                user_id=user_id,
                event_type=event_type,
            )

    actions = event_setting.get("module_actions") or []
    if actions:
        for action in actions:
            module_slug = str(action.get("module") or "").strip()
            if not module_slug:
                continue
            payload = action.get("payload")
            if isinstance(payload, Mapping):
                payload_source = dict(payload)
            else:
                payload_source = payload or {}
            try:
                rendered_payload = await value_templates.render_value_async(payload_source, context)
                if isinstance(rendered_payload, Mapping):
                    payload_data = dict(rendered_payload)
                else:
                    payload_data = {"value": rendered_payload}
                if isinstance(payload_data, Mapping):
                    payload_data.setdefault("context", context)
                await modules_service.trigger_module(
                    module_slug,
                    payload_data,
                    background=False,
                )
            except Exception as exc:  # pragma: no cover - safety around integrations
                logger.error(
                    "Notification module action failed",
                    event_type=event_type,
                    module=module_slug,
                    error=str(exc),
                )
