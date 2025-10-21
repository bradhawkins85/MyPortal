from __future__ import annotations

import asyncio
import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Iterable, Sequence

from loguru import logger

from app.core.config import get_settings
from app.services import webhook_monitor


class EmailDispatchError(Exception):
    """Raised when an email fails to send via SMTP."""


def _normalise_recipients(recipients: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for address in recipients:
        if not address:
            continue
        normalised = address.strip()
        if not normalised:
            continue
        if normalised not in unique:
            unique.append(normalised)
    return unique


async def send_email(
    *,
    subject: str,
    recipients: Sequence[str],
    html_body: str,
    text_body: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    timeout: float = 30.0,
) -> tuple[bool, dict[str, Any] | None]:
    """Send an email using the configured SMTP server.

    Returns a tuple where the first element indicates if delivery was attempted and
    succeeded, and the second element contains the webhook monitor event metadata
    when available.
    """

    settings = get_settings()
    to_addresses = _normalise_recipients(recipients)
    if not to_addresses:
        logger.warning("Email delivery skipped because no recipients were provided", subject=subject)
        return False, None

    if not settings.smtp_host:
        logger.warning("SMTP host not configured; email delivery skipped", subject=subject)
        return False, None

    message = EmailMessage()
    message["Subject"] = subject
    from_address = sender or settings.smtp_user or "no-reply@localhost"
    message["From"] = from_address
    message["To"] = ", ".join(to_addresses)
    if reply_to:
        message["Reply-To"] = reply_to

    if text_body:
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
    else:
        message.set_content(html_body, subtype="html")

    port_segment = f":{settings.smtp_port}" if settings.smtp_port else ""
    endpoint = f"smtp://{settings.smtp_host}{port_segment}"
    event_record: dict[str, Any] | None = None
    event_id: int | None = None
    try:
        event_record = await webhook_monitor.enqueue_event(
            name="email.smtp.send",
            target_url=endpoint,
            payload={
                "subject": subject,
                "recipients": to_addresses,
                "endpoint": endpoint,
            },
            headers={"X-Service": "email"},
            max_attempts=1,
            backoff_seconds=60,
            attempt_immediately=False,
        )
        if event_record.get("id") is not None:
            event_id = int(event_record["id"])
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to enqueue email delivery in webhook monitor",
            subject=subject,
            recipients=to_addresses,
            error=str(exc),
        )

    def _dispatch() -> None:
        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as client:
                client.ehlo()
                if settings.smtp_use_tls:
                    client.starttls(context=context)
                    client.ehlo()
                if settings.smtp_user:
                    client.login(settings.smtp_user, settings.smtp_password or "")
                client.send_message(message)
        except smtplib.SMTPException as exc:  # pragma: no cover - handled in caller
            raise EmailDispatchError(str(exc)) from exc
        except OSError as exc:  # pragma: no cover - handled in caller
            raise EmailDispatchError(str(exc)) from exc

    try:
        await asyncio.to_thread(_dispatch)
    except EmailDispatchError as exc:
        if event_id is not None:
            try:
                event_record = await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="error",
                    error_message=str(exc),
                    response_status=None,
                    response_body=None,
                )
            except Exception as monitor_exc:  # pragma: no cover - defensive logging
                logger.error(
                    "Failed to record SMTP failure in webhook monitor",
                    event_id=event_id,
                    error=str(monitor_exc),
                )
        raise
    else:
        if event_id is not None:
            try:
                response_body = json.dumps(
                    {
                        "recipients": to_addresses,
                        "subject": subject,
                        "endpoint": endpoint,
                    }
                )
                event_record = await webhook_monitor.record_manual_success(
                    event_id,
                    attempt_number=1,
                    response_status=250,
                    response_body=response_body,
                )
            except Exception as monitor_exc:  # pragma: no cover - defensive logging
                logger.error(
                    "Failed to record SMTP success in webhook monitor",
                    event_id=event_id,
                    error=str(monitor_exc),
                )
    logger.info(
        "Email dispatched via SMTP",
        subject=subject,
        recipients=to_addresses,
        sender=message["From"],
        event_id=event_record.get("id") if isinstance(event_record, dict) else None,
    )
    return True, event_record
