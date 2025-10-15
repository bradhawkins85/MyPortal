from __future__ import annotations

from typing import Sequence

from loguru import logger

from app.core.config import get_settings
from app.services import webhook_monitor


class SMSDispatchError(Exception):
    """Raised when an SMS notification fails to dispatch."""


def _normalise_recipients(phone_numbers: Sequence[str]) -> list[str]:
    """Return a de-duplicated list of non-empty phone numbers."""

    unique: list[str] = []
    for number in phone_numbers:
        if not number:
            continue
        normalised = number.strip()
        if not normalised:
            continue
        if normalised not in unique:
            unique.append(normalised)
    return unique


async def send_sms(
    *,
    message: str,
    phone_numbers: Sequence[str],
    sim_number: int = 1,
    ttl: int = 3600,
    priority: int = 100,
    timeout: float = 10.0,
) -> bool:
    """Send an SMS notification using the configured HTTP endpoint.

    Returns ``True`` when a request is dispatched to the remote service, ``False``
    when delivery is skipped because configuration or recipients are missing.
    """

    recipients = _normalise_recipients(phone_numbers)
    if not recipients:
        logger.warning("SMS delivery skipped because no phone numbers were provided")
        return False

    settings = get_settings()
    endpoint = settings.sms_endpoint
    auth = settings.sms_auth.strip() if isinstance(settings.sms_auth, str) else None

    if not endpoint:
        logger.warning(
            "SMS delivery skipped because no endpoint is configured", recipients=recipients
        )
        return False

    if not auth:
        logger.warning(
            "SMS delivery skipped because credentials are not configured",
            endpoint=str(endpoint),
        )
        return False

    payload = {
        "textMessage": {"text": message},
        "phoneNumbers": recipients,
        "simNumber": sim_number,
        "ttl": ttl,
        "priority": priority,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth}",
    }

    try:
        event = await webhook_monitor.enqueue_event(
            name="sms.dispatch",
            target_url=str(endpoint),
            payload=payload,
            headers=headers,
            max_attempts=3,
            backoff_seconds=max(1, int(timeout)) * 30,
            attempt_immediately=True,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        raise SMSDispatchError("Failed to enqueue SMS notification event") from exc

    if not event or not event.get("id"):
        raise SMSDispatchError("SMS notification event was not created")

    status = str(event.get("status") or "").lower()
    last_error = event.get("last_error")

    if status == "failed" or last_error:
        raise SMSDispatchError(last_error or "SMS webhook delivery failed")

    if status == "succeeded":
        logger.info(
            "SMS notification dispatched",
            endpoint=str(endpoint),
            recipient_count=len(recipients),
        )
        return True

    # Pending or in-progress statuses mean the webhook monitor will continue retrying.
    logger.info(
        "SMS notification enqueued for delivery",
        endpoint=str(endpoint),
        recipient_count=len(recipients),
        status=status or "pending",
    )
    return True

