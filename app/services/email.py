from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable, Sequence

from loguru import logger

from app.core.config import get_settings


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
) -> bool:
    """Send an email using the configured SMTP server.

    Returns ``True`` when the message was handed to the SMTP server, ``False`` when
    delivery was skipped because SMTP is not configured.
    """

    settings = get_settings()
    to_addresses = _normalise_recipients(recipients)
    if not to_addresses:
        logger.warning("Email delivery skipped because no recipients were provided", subject=subject)
        return False

    if not settings.smtp_host:
        logger.warning("SMTP host not configured; email delivery skipped", subject=subject)
        return False

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

    await asyncio.to_thread(_dispatch)
    logger.info(
        "Email dispatched via SMTP",
        subject=subject,
        recipients=to_addresses,
        sender=message["From"],
    )
    return True
