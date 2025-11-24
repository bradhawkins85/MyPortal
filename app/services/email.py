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
    enable_tracking: bool = False,
    ticket_reply_id: int | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Send an email using the configured SMTP server.

    Args:
        subject: Email subject line
        recipients: List of recipient email addresses
        html_body: HTML content of the email
        text_body: Plain text version of the email (optional)
        sender: Sender email address (optional, uses SMTP_USER if not provided)
        reply_to: Reply-to email address (optional)
        timeout: SMTP connection timeout in seconds
        enable_tracking: Enable email tracking (opens and clicks)
        ticket_reply_id: ID of the ticket reply being sent (required for tracking)

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

    # Check if SMTP2Go is enabled
    smtp2go_enabled = False
    tracking_id: str | None = None
    modified_html_body = html_body
    
    try:
        from app.services import modules as modules_service
        
        smtp2go_module = await modules_service.get_module("smtp2go", redact=False)
        if smtp2go_module and smtp2go_module.get("enabled"):
            smtp2go_enabled = True
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.debug(
            "SMTP2Go module check failed",
            error=str(exc),
        )
    
    # Use SMTP2Go API if enabled
    if smtp2go_enabled:
        try:
            from app.services import smtp2go
            
            # Generate tracking ID
            tracking_id = smtp2go.generate_tracking_id()
            
            # Send via SMTP2Go API
            result = await smtp2go.send_email_via_api(
                to=to_addresses,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                sender=sender,
                reply_to=reply_to,
                tracking_id=tracking_id,
            )

            # Record tracking metadata if ticket reply ID provided
            smtp2go_message_id = result.get("smtp2go_message_id") or result.get("email_id")
            response_tracking_id = result.get("tracking_id") or tracking_id
            # Store smtp2go_message_id and tracking_id for webhook correlation
            # email_sent_at will be set when the 'processed' webhook event arrives
            if ticket_reply_id and response_tracking_id and smtp2go_message_id:
                await smtp2go.record_smtp2go_message_id(
                    ticket_reply_id=ticket_reply_id,
                    tracking_id=response_tracking_id,
                    smtp2go_message_id=smtp2go_message_id,
                )
            elif ticket_reply_id:
                logger.warning(
                    "SMTP2Go email sent but tracking data not available for storage",
                    ticket_reply_id=ticket_reply_id,
                    has_smtp2go_message_id=smtp2go_message_id is not None,
                    has_tracking_id=response_tracking_id is not None,
                )
            
            logger.info(
                "Email dispatched via SMTP2Go API",
                subject=subject,
                recipients=to_addresses,
                smtp2go_message_id=smtp2go_message_id,
                tracking_enabled=True,
            )
            
            # Return success with SMTP2Go response
            return True, {
                "id": smtp2go_message_id,
                "status": "succeeded",
                "provider": "smtp2go",
            }
            
        except Exception as exc:
            logger.error(
                "SMTP2Go API delivery failed, falling back to SMTP relay",
                subject=subject,
                recipients=to_addresses,
                error=str(exc),
            )
            # Fall through to SMTP relay
    
    # Apply email tracking if enabled (legacy Plausible tracking)
    tracking_requested = enable_tracking
    module_settings: dict[str, Any] | None = None
    try:
        from app.services import modules as modules_service

        plausible_module = await modules_service.get_module("plausible", redact=False)
        if plausible_module and plausible_module.get("enabled"):
            tracking_requested = True

        # Load module settings when tracking is requested or the module is enabled
        if tracking_requested or (plausible_module and plausible_module.get("enabled")):
            try:
                module_settings = await modules_service.get_module_settings("plausible")
            except Exception as settings_exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "Plausible settings unavailable; using tracking defaults",
                    reply_id=ticket_reply_id,
                    error=str(settings_exc),
                )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "Plausible settings unavailable; tracking fallback in use",
            reply_id=ticket_reply_id,
            error=str(exc),
        )

    if tracking_requested:
        try:
            from app.services import email_tracking

            # Check if portal_url is configured (required for tracking)
            if not settings.portal_url:
                logger.warning(
                    "Email tracking requested but PORTAL_URL is not configured",
                    reply_id=ticket_reply_id,
                )
            else:
                track_opens = module_settings.get("track_opens", True) if module_settings else True
                track_clicks = module_settings.get("track_clicks", True) if module_settings else True

                if track_opens or track_clicks:
                    tracking_id = email_tracking.generate_tracking_id()

                    # Insert tracking pixel for open tracking
                    if track_opens:
                        modified_html_body = email_tracking.insert_tracking_pixel(modified_html_body, tracking_id)

                    # Rewrite links for click tracking
                    if track_clicks:
                        modified_html_body = email_tracking.rewrite_links_for_tracking(modified_html_body, tracking_id)

                    logger.info(
                        "Email tracking enabled",
                        tracking_id=tracking_id,
                        reply_id=ticket_reply_id,
                        track_opens=track_opens,
                        track_clicks=track_clicks,
                    )
                else:
                    logger.info(
                        "Email tracking disabled by Plausible module settings",
                        reply_id=ticket_reply_id,
                        track_opens=track_opens,
                        track_clicks=track_clicks,
                    )
        except Exception as exc:
            logger.error(
                "Failed to apply email tracking",
                reply_id=ticket_reply_id,
                error=str(exc),
            )
            # Continue without tracking rather than failing the email send
            modified_html_body = html_body
            tracking_id = None

    message = EmailMessage()
    message["Subject"] = subject
    from_address = sender or settings.smtp_user or "no-reply@localhost"
    message["From"] = from_address
    message["To"] = ", ".join(to_addresses)
    if reply_to:
        message["Reply-To"] = reply_to

    if text_body:
        message.set_content(text_body)
        if modified_html_body:
            message.add_alternative(modified_html_body, subtype="html")
    else:
        message.set_content(modified_html_body, subtype="html")

    port_segment = f":{settings.smtp_port}" if settings.smtp_port else ""
    endpoint = f"smtp://{settings.smtp_host}{port_segment}"
    event_record: dict[str, Any] | None = None
    event_id: int | None = None
    try:
        event_record = await webhook_monitor.create_manual_event(
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
    
    # Record email tracking metadata if tracking is enabled
    if tracking_id and ticket_reply_id:
        try:
            from app.services import email_tracking
            await email_tracking.record_email_sent(ticket_reply_id, tracking_id)
        except Exception as tracking_exc:
            logger.error(
                "Failed to record email tracking metadata",
                reply_id=ticket_reply_id,
                tracking_id=tracking_id,
                error=str(tracking_exc),
            )
    
    logger.info(
        "Email dispatched via SMTP",
        subject=subject,
        recipients=to_addresses,
        sender=message["From"],
        event_id=event_record.get("id") if isinstance(event_record, dict) else None,
        tracking_enabled=tracking_id is not None,
    )
    return True, event_record
