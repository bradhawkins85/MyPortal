from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Iterable, Mapping, Sequence

from loguru import logger

from app.core.config import get_settings
from app.services import webhook_monitor


class EmailDispatchError(Exception):
    """Raised when an email fails to send via SMTP."""


def _normalise_attachment(attachment: Mapping[str, Any]) -> tuple[str, bytes, str | None]:
    filename = (
        str(attachment.get("filename") or attachment.get("name") or "attachment").strip()
        or "attachment"
    )
    content = attachment.get("content")
    if isinstance(content, str):
        content_bytes = base64.b64decode(content)
    elif isinstance(content, bytes):
        content_bytes = content
    elif isinstance(content, bytearray):
        content_bytes = bytes(content)
    else:
        raise ValueError(f"Attachment {filename!r} does not include bytes or base64 content")
    mime_type = str(
        attachment.get("mime_type")
        or attachment.get("mimetype")
        or attachment.get("content_type")
        or ""
    ).strip()
    if not mime_type:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return filename, content_bytes, mime_type


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
    attachments: Sequence[Mapping[str, Any]] | None = None,
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
        attachments: Optional file attachments. Content may be bytes or base64 text.

    Returns a tuple where the first element indicates if delivery was attempted and
    succeeded, and the second element contains the webhook monitor event metadata
    when available.
    """

    settings = get_settings()
    to_addresses = _normalise_recipients(recipients)
    try:
        from app.repositories import email_blocklist as email_blocklist_repo

        to_addresses, blocked_addresses = await email_blocklist_repo.filter_allowed(to_addresses)
        if blocked_addresses:
            logger.info("Email blocklist suppressed recipients", subject=subject, blocked_recipients=blocked_addresses)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Email blocklist check failed; continuing with original recipients", subject=subject, error=str(exc))
    if not to_addresses:
        logger.warning("Email delivery skipped because no recipients were provided", subject=subject)
        return False, None

    # M365 notification handling is evaluated before SMTP. Its result describes
    # the actual Graph operation (created Inbox draft or submitted mail), not a
    # delivery confirmation.
    try:
        from app.services import modules as module_service
        direct_module = await module_service.get_module(
            "m365-direct-delivery", redact=False
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        direct_module = None
        logger.debug("M365 direct-delivery module check failed", error=str(exc))
    if direct_module and direct_module.get("enabled"):
        direct_settings = dict(direct_module.get("settings") or {})
        try:
            direct_company_id = int(direct_settings.get("company_id") or 0)
        except (TypeError, ValueError):
            direct_company_id = 0
        configured_domains = {
            str(domain).strip().lower().lstrip("@")
            for domain in direct_settings.get("recipient_domains") or []
            if str(domain).strip()
        }
        direct_recipients = [
            address for address in to_addresses
            if not configured_domains or address.rsplit("@", 1)[-1].lower() in configured_domains
        ]
        delivered: list[str] = []
        outcomes: list[dict[str, Any]] = []
        direct_errors: list[tuple[str, Exception]] = []
        if direct_company_id:
            from app.services import m365_direct_delivery

            for address in direct_recipients:
                try:
                    mode = str(direct_settings.get("delivery_mode") or "inbox_item")
                    configured_sender = str(direct_settings.get("sender_address") or "").strip()
                    from app.services import email_recipients
                    if ticket_reply_id:
                        prior = await email_recipients.get_m365_terminal_operation(
                            reply_id=ticket_reply_id, recipient_email=address,
                        )
                        if prior:
                            delivered.append(address)
                            outcomes.append(prior)
                            continue
                    result = await m365_direct_delivery.deliver_message(
                        company_id=direct_company_id,
                        recipient=address,
                        subject=subject,
                        html_body=html_body,
                        text_body=text_body,
                        sender=configured_sender or (sender if mode == "inbox_item" else None),
                        reply_to=reply_to,
                        attachments=attachments,
                        mode=mode,
                    )
                    if ticket_reply_id:
                        await email_recipients.record_m365_operation(
                            reply_id=ticket_reply_id,
                            recipient_email=address,
                            company_id=direct_company_id,
                            message_id=result.get("message_id"),
                            operation=result["operation"], state=result["state"],
                        )
                    delivered.append(address)
                    outcomes.append(result)
                except Exception as exc:
                    direct_errors.append((address, exc))
                    if ticket_reply_id:
                        try:
                            from app.services import email_recipients
                            await email_recipients.record_m365_operation(
                                reply_id=ticket_reply_id, recipient_email=address,
                                company_id=direct_company_id, message_id=None,
                                operation=str(direct_settings.get("delivery_mode") or "inbox_item"),
                                state=("unknown" if isinstance(
                                    exc, m365_direct_delivery.AmbiguousDeliveryError
                                ) else "failed"),
                            )
                        except Exception as history_exc:  # pragma: no cover - defensive
                            logger.warning("Unable to record M365 failure", error=str(history_exc))
                    logger.warning(
                        "M365 direct delivery failed",
                        recipient=address, subject=subject, error=str(exc),
                    )
        elif direct_recipients:
            logger.error("M365 direct delivery is enabled without a company_id")
        to_addresses = [address for address in to_addresses if address not in delivered]
        # sendMail may have reached Exchange before a timeout. Falling back to
        # SMTP could duplicate it and would silently change transport.
        if direct_errors and (direct_settings.get("delivery_mode") == "send_mail" or
                              not direct_settings.get("fallback_to_smtp", True)):
            failed = ", ".join(address for address, _ in direct_errors)
            raise EmailDispatchError(f"M365 direct delivery failed for: {failed}")
        if not to_addresses:
            logger.info(
                "M365 notification operation completed",
                subject=subject, recipients=delivered, tracking=bool(ticket_reply_id),
            )
            return True, {
                "status": outcomes[0]["state"] if len(outcomes) == 1 else "completed",
                "operation": outcomes[0]["operation"] if len(outcomes) == 1 else "mixed",
                "provider": "m365-direct-delivery", "recipients": delivered,
            }

    if not settings.smtp_host:
        logger.warning("SMTP host not configured; email delivery skipped", subject=subject)
        return False, None

    # Check if SMTP2Go is enabled
    smtp2go_enabled = False
    tracking_id: str | None = None
    modified_html_body = html_body
    
    try:
        from app.services import modules as module_service
        smtp2go_module = await module_service.get_module(
            "smtp2go", redact=False
        )
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
                bcc=(
                    [str(settings.outbound_audit_bcc)]
                    if settings.outbound_audit_bcc
                    else None
                ),
                tracking_id=tracking_id,
                attachments=[
                    {"filename": name, "content": base64.b64encode(content).decode("ascii")}
                    for name, content, _mime in (_normalise_attachment(item) for item in (attachments or []))
                ] or None,
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

            # Record per-recipient rows so the delivery-status badge popup
            # can break delivery down per recipient. email_sent_at is left
            # NULL here for the SMTP2Go path; it will be stamped when the
            # 'processed' webhook event arrives, mirroring the aggregate
            # ticket_replies.email_sent_at behaviour.
            if ticket_reply_id:
                try:
                    from app.services import email_recipients

                    await email_recipients.record_recipients(
                        reply_id=ticket_reply_id,
                        tracking_id=response_tracking_id,
                        smtp2go_message_id=smtp2go_message_id,
                        to=to_addresses,
                        sent_at=None,
                    )
                except Exception as recipients_exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Failed to record per-recipient rows for SMTP2Go send",
                        reply_id=ticket_reply_id,
                        error=str(recipients_exc),
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
    
    # Apply email tracking when requested by the caller.
    tracking_requested = enable_tracking
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
                tracking_id = email_tracking.generate_tracking_id()
                modified_html_body = email_tracking.insert_tracking_pixel(
                    modified_html_body, tracking_id
                )
                modified_html_body = email_tracking.rewrite_links_for_tracking(
                    modified_html_body, tracking_id
                )

                logger.info(
                    "Email tracking enabled",
                    tracking_id=tracking_id,
                    reply_id=ticket_reply_id,
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
    from_address = sender or settings.smtp_from or settings.smtp_user or "no-reply@localhost"
    message["From"] = from_address
    message["To"] = ", ".join(to_addresses)
    if settings.outbound_audit_bcc:
        message["Bcc"] = str(settings.outbound_audit_bcc)
    if reply_to:
        message["Reply-To"] = reply_to

    if text_body:
        message.set_content(text_body)
        if modified_html_body:
            message.add_alternative(modified_html_body, subtype="html")
    else:
        message.set_content(modified_html_body, subtype="html")

    for attachment in attachments or []:
        filename, content_bytes, mime_type = _normalise_attachment(attachment)
        maintype, _, subtype = mime_type.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            content_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

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
                await webhook_monitor.record_manual_failure(
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

    # Record per-recipient rows for the SMTP relay path. Stamp email_sent_at
    # immediately because the SMTP relay path has no asynchronous webhook
    # confirmation — once smtp.send_message returns, the message is gone.
    if ticket_reply_id:
        try:
            from app.services import email_recipients

            await email_recipients.record_recipients(
                reply_id=ticket_reply_id,
                tracking_id=tracking_id,
                smtp2go_message_id=None,
                to=to_addresses,
            )
        except Exception as recipients_exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to record per-recipient rows for SMTP send",
                reply_id=ticket_reply_id,
                error=str(recipients_exc),
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
