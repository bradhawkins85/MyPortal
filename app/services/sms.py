from __future__ import annotations

from typing import Sequence

import httpx
from loguru import logger

from app.core.config import get_settings


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
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(str(endpoint), json=payload, headers=headers)
    except httpx.HTTPError as exc:  # pragma: no cover - surface via caller
        raise SMSDispatchError("Failed to dispatch SMS notification") from exc

    if not 200 <= response.status_code < 300:
        raise SMSDispatchError(f"Unexpected SMS gateway response: HTTP {response.status_code}")

    logger.info(
        "SMS notification dispatched",
        endpoint=str(endpoint),
        recipient_count=len(recipients),
    )
    return True

