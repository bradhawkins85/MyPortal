"""SMTP2Go API service for enhanced email delivery and tracking.

Provides functionality for:
- Sending emails via SMTP2Go API
- Processing webhooks for delivery, open, and click events
- Tracking email status and events
- Pre-defined email templates for common use cases
"""

from __future__ import annotations

from html import escape as html_escape
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from loguru import logger

from app.core.config import get_settings
from app.core.database import db


class SMTP2GoError(Exception):
    """Raised when SMTP2Go API request fails."""


# Email payload templates for common use cases
EmailTemplateType = Literal[
    "password_reset",
    "invoice",
    "alert",
    "notification",
    "ticket_reply",
    "welcome",
]

EMAIL_TEMPLATES: dict[EmailTemplateType, dict[str, Any]] = {
    "password_reset": {
        "subject_template": "Password Reset Request",
        "recommended_fields": ["recipient_name", "reset_link", "expiry_time"],
        "example_payload": {
            "recipients": ["user@example.com"],
            "subject": "Password Reset Request",
            "html": "<p>Hello {recipient_name},</p><p>Click here to reset your password: <a href='{reset_link}'>Reset Password</a></p><p>This link expires in {expiry_time}.</p>",
            "text": "Hello {recipient_name},\n\nClick here to reset your password: {reset_link}\n\nThis link expires in {expiry_time}.",
        },
        "description": "Template for password reset emails with secure reset link",
    },
    "invoice": {
        "subject_template": "Invoice #{invoice_number} - {company_name}",
        "recommended_fields": ["invoice_number", "company_name", "amount", "due_date", "invoice_link"],
        "example_payload": {
            "recipients": ["customer@example.com"],
            "subject": "Invoice #{invoice_number} - {company_name}",
            "html": "<p>Dear Customer,</p><p>Please find your invoice #{invoice_number} for ${amount}.</p><p>Due date: {due_date}</p><p><a href='{invoice_link}'>View Invoice</a></p>",
            "text": "Dear Customer,\n\nPlease find your invoice #{invoice_number} for ${amount}.\n\nDue date: {due_date}\n\nView Invoice: {invoice_link}",
        },
        "description": "Template for invoice notification emails",
    },
    "alert": {
        "subject_template": "Alert: {alert_type}",
        "recommended_fields": ["alert_type", "alert_message", "severity", "timestamp", "action_link"],
        "example_payload": {
            "recipients": ["admin@example.com"],
            "subject": "Alert: {alert_type}",
            "html": "<p><strong>{alert_type}</strong></p><p>Severity: {severity}</p><p>{alert_message}</p><p>Time: {timestamp}</p><p><a href='{action_link}'>Take Action</a></p>",
            "text": "{alert_type}\n\nSeverity: {severity}\n\n{alert_message}\n\nTime: {timestamp}\n\nTake Action: {action_link}",
        },
        "description": "Template for system alerts and notifications",
    },
    "notification": {
        "subject_template": "Notification: {title}",
        "recommended_fields": ["title", "message", "action_text", "action_link"],
        "example_payload": {
            "recipients": ["user@example.com"],
            "subject": "Notification: {title}",
            "html": "<p><strong>{title}</strong></p><p>{message}</p><p><a href='{action_link}'>{action_text}</a></p>",
            "text": "{title}\n\n{message}\n\n{action_text}: {action_link}",
        },
        "description": "General purpose notification template",
    },
    "ticket_reply": {
        "subject_template": "Re: Ticket #{ticket_id} - {ticket_subject}",
        "recommended_fields": ["ticket_id", "ticket_subject", "reply_content", "reply_author", "ticket_link"],
        "example_payload": {
            "recipients": ["customer@example.com"],
            "subject": "Re: Ticket #{ticket_id} - {ticket_subject}",
            "html": "<p>{reply_author} replied to your ticket:</p><div>{reply_content}</div><p><a href='{ticket_link}'>View Ticket</a></p>",
            "text": "{reply_author} replied to your ticket:\n\n{reply_content}\n\nView Ticket: {ticket_link}",
        },
        "description": "Template for support ticket reply notifications",
    },
    "welcome": {
        "subject_template": "Welcome to {company_name}!",
        "recommended_fields": ["recipient_name", "company_name", "login_link", "support_email"],
        "example_payload": {
            "recipients": ["newuser@example.com"],
            "subject": "Welcome to {company_name}!",
            "html": "<p>Hello {recipient_name},</p><p>Welcome to {company_name}!</p><p><a href='{login_link}'>Get Started</a></p><p>Need help? Contact us at {support_email}</p>",
            "text": "Hello {recipient_name},\n\nWelcome to {company_name}!\n\nGet Started: {login_link}\n\nNeed help? Contact us at {support_email}",
        },
        "description": "Template for new user welcome emails",
    },
}


def generate_tracking_id() -> str:
    """Generate a unique tracking ID for email tracking.
    
    Returns a URL-safe random token.
    """
    return secrets.token_urlsafe(32)


def get_email_template(template_type: EmailTemplateType) -> dict[str, Any]:
    """Get an email template by type.
    
    Args:
        template_type: The type of email template to retrieve
        
    Returns:
        Dict containing template information including example payload
        
    Raises:
        ValueError: If template_type is not recognized
    """
    if template_type not in EMAIL_TEMPLATES:
        valid_types = ", ".join(EMAIL_TEMPLATES.keys())
        raise ValueError(
            f"Unknown template type: {template_type}. "
            f"Valid types are: {valid_types}"
        )
    
    return EMAIL_TEMPLATES[template_type].copy()


def list_email_templates() -> list[dict[str, Any]]:
    """List all available email templates.
    
    Returns:
        List of template information dicts with type, description, and fields
    """
    return [
        {
            "type": template_type,
            "description": template_info["description"],
            "subject_template": template_info["subject_template"],
            "recommended_fields": template_info["recommended_fields"],
        }
        for template_type, template_info in EMAIL_TEMPLATES.items()
    ]


def _substitute_variables(template_str: str, variables: dict[str, Any]) -> str:
    """Safely substitute variables in a template string.
    
    Args:
        template_str: Template string with {variable_name} placeholders
        variables: Dictionary of variable values to substitute
        
    Returns:
        String with variables substituted
        
    Note:
        This is a simple string replacement. For complex templates,
        consider using Jinja2 template engine instead.
    """
    result = template_str
    for key, value in variables.items():
        # Escape HTML in variable values to prevent XSS
        safe_value = html_escape(str(value))
        placeholder = "{" + key + "}"
        result = result.replace(placeholder, safe_value)
    return result


def format_template_payload(
    template_type: EmailTemplateType,
    variables: dict[str, Any],
    recipients: list[str],
    sender: str | None = None,
) -> dict[str, Any]:
    """Format an email payload using a template.
    
    Args:
        template_type: The type of template to use
        variables: Dictionary of variables to substitute in the template
        recipients: List of recipient email addresses
        sender: Optional sender email address
        
    Returns:
        Dict containing formatted email payload ready for send_email_via_api
        
    Raises:
        ValueError: If template_type is not recognized
        
    Example:
        >>> payload = format_template_payload(
        ...     "password_reset",
        ...     {
        ...         "recipient_name": "John Doe",
        ...         "reset_link": "https://example.com/reset/token123",
        ...         "expiry_time": "1 hour",
        ...     },
        ...     ["user@example.com"],
        ... )
        >>> result = await send_email_via_api(**payload)
    """
    template = get_email_template(template_type)
    example = template["example_payload"]
    
    # Format subject with variables
    subject = _substitute_variables(template["subject_template"], variables)
    
    # Format HTML body with variables
    html_body = _substitute_variables(example.get("html", ""), variables)
    
    # Format text body with variables
    text_body = _substitute_variables(example.get("text", ""), variables)
    
    # Build payload
    payload: dict[str, Any] = {
        "to": recipients,
        "subject": subject,
        "html_body": html_body,
    }
    
    if text_body:
        payload["text_body"] = text_body
    
    if sender:
        payload["sender"] = sender
    
    return payload


async def send_email_via_api(
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    custom_headers: dict[str, str] | None = None,
    attachments: list[dict[str, str]] | None = None,
    template_id: str | None = None,
    template_data: dict[str, Any] | None = None,
    tracking_id: str | None = None,
) -> dict[str, Any]:
    """Send an email using SMTP2Go API.
    
    Args:
        to: List of recipient email addresses
        subject: Email subject line
        html_body: HTML content of the email
        text_body: Plain text version of the email (optional)
        sender: Sender email address (optional, but highly recommended)
        reply_to: Reply-to email address (optional)
        cc: List of CC recipient email addresses (optional)
        bcc: List of BCC recipient email addresses (optional)
        custom_headers: Additional email headers (optional)
        attachments: List of attachment dicts with 'filename' and 'content' (base64) (optional)
        template_id: SMTP2Go template ID to use (optional)
        template_data: Template variables for SMTP2Go template (optional)
        tracking_id: Internal tracking ID to associate with this email (optional)
        
    Returns:
        Dict containing the API response with message_id and status
        
    Raises:
        SMTP2GoError: If the API request fails
    """
    settings = get_settings()
    
    # Get SMTP2Go configuration from integration module
    from app.services import modules as modules_service
    
    try:
        module_settings = await modules_service.get_module_settings('smtp2go')
        if not module_settings:
            raise SMTP2GoError("SMTP2Go module not configured")
        
        api_key = module_settings.get('api_key')
        if not api_key:
            raise SMTP2GoError("SMTP2Go API key not configured")
        
        # Validate required fields
        if not to or len(to) == 0:
            raise SMTP2GoError("At least one recipient email address is required")
        
        if not subject:
            raise SMTP2GoError("Email subject is required")
        
        if not html_body and not text_body:
            raise SMTP2GoError("Email body (html_body or text_body) is required")
        
        # Determine sender - REQUIRED by SMTP2Go API
        sender_address = sender or settings.smtp_user
        if not sender_address:
            raise SMTP2GoError(
                "Sender email address is required. "
                "Provide 'sender' parameter or configure SMTP_USER in settings."
            )
        
        # Build request payload
        payload = {
            "api_key": api_key,
            "to": to,
            "sender": sender_address,
            "subject": subject,
            "html_body": html_body,
        }
        
        # Add optional fields
        if text_body:
            payload["text_body"] = text_body
        
        # Add CC recipients
        if cc and len(cc) > 0:
            payload["cc"] = cc
        
        # Add BCC recipients
        if bcc and len(bcc) > 0:
            payload["bcc"] = bcc
        
        # Add attachments
        if attachments and len(attachments) > 0:
            payload["attachments"] = attachments
        
        # Add SMTP2Go template fields
        if template_id:
            payload["template_id"] = template_id
        
        if template_data:
            payload["template_data"] = template_data
        
        if reply_to:
            payload["custom_headers"] = payload.get("custom_headers", [])
            payload["custom_headers"].append({
                "header": "Reply-To",
                "value": reply_to
            })
        
        # Add custom headers
        if custom_headers:
            payload["custom_headers"] = payload.get("custom_headers", [])
            for header, value in custom_headers.items():
                payload["custom_headers"].append({
                    "header": header,
                    "value": value
                })
        
        # Add tracking ID as custom header for internal correlation
        if tracking_id:
            payload["custom_headers"] = payload.get("custom_headers", [])
            payload["custom_headers"].append({
                "header": "X-Tracking-ID",
                "value": tracking_id
            })
        
        # Send request to SMTP2Go API
        api_url = "https://api.smtp2go.com/v3/email/send"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(api_url, json=payload)
            
            # Log detailed error information for 400 Bad Request
            if response.status_code == 400:
                try:
                    error_detail = response.json()
                except Exception:
                    error_detail = response.text
                
                logger.error(
                    "SMTP2Go API returned 400 Bad Request",
                    subject=subject,
                    recipients=to,
                    sender=sender_address,
                    status_code=response.status_code,
                    error_response=error_detail,
                    payload_keys=list(payload.keys()),
                )
                raise SMTP2GoError(
                    f"API request failed with 400 Bad Request. "
                    f"Response: {error_detail}. "
                    f"Check that all required fields are present and valid."
                )
            
            response.raise_for_status()
            result = response.json()

        # SMTP2Go responses may include the data directly at the top level or
        # within a "data" envelope. Normalise this so downstream logic has a
        # consistent dict to work with.
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            data = result if isinstance(result, dict) else {}

        # Normalise message ID field for downstream tracking logic
        message_id = (
            data.get("smtp2go_message_id")
            or data.get("email_id")
            or data.get("message_id")
            or data.get("messageid")
            or data.get("request_id")
        )
        if message_id and not data.get("email_id"):
            data["email_id"] = message_id
        if message_id and not data.get("smtp2go_message_id"):
            data["smtp2go_message_id"] = message_id

        # Normalise tracking ID so callers can persist it
        response_tracking_id = data.get("tracking_id") or tracking_id
        if response_tracking_id and not data.get("tracking_id"):
            data["tracking_id"] = response_tracking_id

        # Check if request was successful. SMTP2Go returns different shapes for
        # successful responses: some include error_code="SUCCESS", others just
        # return succeeded/failed counts with no error_code field. Treat any 200
        # response without explicit errors or failures as success.
        error_code = data.get("error_code")
        error_msg = data.get("error")
        errors_list = data.get("errors")
        failed_count = data.get("failed")
        result_status = data.get("result")

        success_response = (
            (error_code is None or error_code == "SUCCESS")
            and not error_msg
            and (not errors_list or len(errors_list) == 0)
            and (failed_count is None or failed_count == 0)
            and (result_status is None or str(result_status).lower() == "success")
        )

        if not success_response:
            error_msg = error_msg or (
                errors_list[0] if isinstance(errors_list, list) and errors_list else "Unknown error"
            )
            error_code = error_code or "UNKNOWN"
            logger.error(
                "SMTP2Go API returned error",
                subject=subject,
                recipients=to,
                error_code=error_code,
                error_message=error_msg,
            )
            raise SMTP2GoError(f"SMTP2Go API error [{error_code}]: {error_msg}")

        logger.info(
            "Email sent via SMTP2Go API",
            subject=subject,
            recipients=to,
            sender=sender_address,
            message_id=data.get("email_id"),
            tracking_id=response_tracking_id,
        )

        return data
        
    except SMTP2GoError:
        # Re-raise SMTP2GoError exceptions without wrapping
        raise
    except httpx.HTTPError as exc:
        # Enhanced error logging for HTTP errors
        response_text = None
        status_code = None
        if hasattr(exc, 'response') and exc.response is not None:
            status_code = exc.response.status_code
            try:
                response_text = exc.response.text
            except Exception:
                pass
        
        logger.error(
            "SMTP2Go API HTTP error",
            subject=subject,
            recipients=to,
            sender=sender_address,
            status_code=status_code,
            error=str(exc),
            response_text=response_text,
        )
        raise SMTP2GoError(f"API request failed: {str(exc)}") from exc
    except Exception as exc:
        logger.error(
            "Failed to send email via SMTP2Go",
            subject=subject,
            recipients=to,
            error=str(exc),
        )
        raise SMTP2GoError(f"Send failed: {str(exc)}") from exc


async def record_email_sent(
    *,
    ticket_reply_id: int,
    tracking_id: str,
    smtp2go_message_id: str | None = None,
) -> None:
    """Record that an email was sent with SMTP2Go tracking enabled.
    
    Args:
        ticket_reply_id: ID of the ticket reply that was sent
        tracking_id: Unique tracking ID for this email
        smtp2go_message_id: SMTP2Go message ID returned from API
    """
    query = """
        UPDATE ticket_replies
        SET email_tracking_id = :tracking_id,
            email_sent_at = :sent_at,
            smtp2go_message_id = :smtp2go_message_id
        WHERE id = :reply_id
    """
    params = {
        'tracking_id': tracking_id,
        'sent_at': datetime.now(timezone.utc),
        'smtp2go_message_id': smtp2go_message_id,
        'reply_id': ticket_reply_id,
    }
    
    try:
        await db.execute(query, params)
        logger.info(
            "Recorded SMTP2Go email metadata",
            reply_id=ticket_reply_id,
            tracking_id=tracking_id,
            smtp2go_message_id=smtp2go_message_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to record SMTP2Go email metadata",
            reply_id=ticket_reply_id,
            tracking_id=tracking_id,
            error=str(exc),
        )


async def process_webhook_event(
    event_type: str | None,
    event_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Process a webhook event from SMTP2Go.
    
    Args:
        event_type: Type of event (delivered, opened, clicked, bounced, etc.)
        event_data: Event payload from SMTP2Go
        
    Returns:
        Event record as a dict, or None if processing failed
    """
    normalized_event_type = (event_type or "").lower()

    # Extract relevant fields from webhook
    smtp2go_message_id = event_data.get("email_id")
    recipient = event_data.get("recipient")
    timestamp_str = event_data.get("timestamp")
    
    # Parse timestamp
    occurred_at = datetime.now(timezone.utc)
    if timestamp_str:
        try:
            occurred_at = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            logger.warning(
                "Failed to parse webhook timestamp",
                timestamp=timestamp_str,
            )
    
    # Look up ticket reply by SMTP2Go message ID
    lookup_query = """
        SELECT id, email_tracking_id
        FROM ticket_replies
        WHERE smtp2go_message_id = :smtp2go_message_id
        LIMIT 1
    """
    
    try:
        reply = await db.fetch_one(lookup_query, {'smtp2go_message_id': smtp2go_message_id})
        if not reply:
            logger.warning(
                "Webhook received for unknown SMTP2Go message",
                smtp2go_message_id=smtp2go_message_id,
                event_type=event_type,
            )
            return None
        
        tracking_id = reply['email_tracking_id']
        reply_id = reply['id']
        
        # Map SMTP2Go event types to our event types
        internal_event_type = None
        event_url = None
        
        if normalized_event_type in ['processed']:
            internal_event_type = 'processed'
        elif normalized_event_type in ['delivered', 'delivery']:
            internal_event_type = 'delivered'
        elif normalized_event_type in ['opened', 'open']:
            internal_event_type = 'open'
        elif normalized_event_type in ['clicked', 'click']:
            internal_event_type = 'click'
            event_url = event_data.get('url')
        elif normalized_event_type in ['bounced', 'bounce']:
            internal_event_type = 'bounce'
        elif normalized_event_type in ['spam', 'spam_complaint']:
            internal_event_type = 'spam'
        elif normalized_event_type in ['rejected']:
            internal_event_type = 'rejected'

        if not internal_event_type:
            logger.warning(
                "Unknown SMTP2Go event type",
                event_type=event_type,
                smtp2go_message_id=smtp2go_message_id,
            )
            return None
        
        # Insert tracking event
        insert_query = """
            INSERT INTO email_tracking_events
            (tracking_id, event_type, event_url, user_agent, ip_address, occurred_at, smtp2go_data)
            VALUES (:tracking_id, :event_type, :event_url, :user_agent, :ip_address, :occurred_at, :smtp2go_data)
        """
        insert_params = {
            'tracking_id': tracking_id,
            'event_type': internal_event_type,
            'event_url': event_url,
            'user_agent': event_data.get('user_agent'),
            'ip_address': event_data.get('ip'),
            'occurred_at': occurred_at,
            'smtp2go_data': json.dumps(event_data) if event_data else None,  # Store full webhook data for debugging
        }
        
        event_id = await db.execute(insert_query, insert_params)
        
        # Update ticket_replies based on event type
        if internal_event_type == 'processed':
            update_query = """
                UPDATE ticket_replies
                SET email_processed_at = COALESCE(email_processed_at, :occurred_at)
                WHERE id = :reply_id
            """
            await db.execute(update_query, {'occurred_at': occurred_at, 'reply_id': reply_id})
        elif internal_event_type == 'delivered':
            update_query = """
                UPDATE ticket_replies
                SET email_delivered_at = COALESCE(email_delivered_at, :occurred_at)
                WHERE id = :reply_id
            """
            await db.execute(update_query, {'occurred_at': occurred_at, 'reply_id': reply_id})
        elif internal_event_type == 'open':
            update_query = """
                UPDATE ticket_replies
                SET email_opened_at = COALESCE(email_opened_at, :occurred_at),
                    email_open_count = email_open_count + 1
                WHERE id = :reply_id
            """
            await db.execute(update_query, {'occurred_at': occurred_at, 'reply_id': reply_id})
        elif internal_event_type == 'bounce':
            update_query = """
                UPDATE ticket_replies
                SET email_bounced_at = COALESCE(email_bounced_at, :occurred_at)
                WHERE id = :reply_id
            """
            await db.execute(update_query, {'occurred_at': occurred_at, 'reply_id': reply_id})
        elif internal_event_type == 'rejected':
            update_query = """
                UPDATE ticket_replies
                SET email_rejected_at = COALESCE(email_rejected_at, :occurred_at)
                WHERE id = :reply_id
            """
            await db.execute(update_query, {'occurred_at': occurred_at, 'reply_id': reply_id})
        
        logger.info(
            "Processed SMTP2Go webhook event",
            event_id=event_id,
            tracking_id=tracking_id,
            event_type=internal_event_type,
            smtp2go_message_id=smtp2go_message_id,
        )
        
        return {
            'id': event_id,
            'tracking_id': tracking_id,
            'event_type': internal_event_type,
            'occurred_at': occurred_at,
        }
        
    except Exception as exc:
        logger.error(
            "Failed to process SMTP2Go webhook",
            event_type=event_type,
            smtp2go_message_id=smtp2go_message_id,
            error=str(exc),
        )
        return None


async def get_email_stats(reply_id: int) -> dict[str, Any] | None:
    """Get email delivery and tracking statistics for a ticket reply.
    
    Args:
        reply_id: ID of the ticket reply
        
    Returns:
        Dict with email statistics, or None if not found
    """
    query = """
        SELECT 
            email_tracking_id,
            smtp2go_message_id,
            email_sent_at,
            email_processed_at,
            email_delivered_at,
            email_opened_at,
            email_open_count,
            email_bounced_at,
            email_rejected_at
        FROM ticket_replies
        WHERE id = :reply_id
        LIMIT 1
    """
    
    try:
        row = await db.fetch_one(query, {'reply_id': reply_id})
        if not row:
            return None
        
        # Get click events
        clicks_query = """
            SELECT event_url, occurred_at
            FROM email_tracking_events
            WHERE tracking_id = :tracking_id
                AND event_type = 'click'
            ORDER BY occurred_at DESC
        """
        clicks = []
        if row['email_tracking_id']:
            click_rows = await db.fetch_all(clicks_query, {'tracking_id': row['email_tracking_id']})
            clicks = [
                {
                    'url': click['event_url'],
                    'clicked_at': click['occurred_at'].isoformat() if click['occurred_at'] else None,
                }
                for click in click_rows
            ]
        
        return {
            'tracking_id': row['email_tracking_id'],
            'smtp2go_message_id': row['smtp2go_message_id'],
            'sent_at': row['email_sent_at'].isoformat() if row['email_sent_at'] else None,
            'processed_at': row['email_processed_at'].isoformat() if row['email_processed_at'] else None,
            'delivered_at': row['email_delivered_at'].isoformat() if row['email_delivered_at'] else None,
            'opened_at': row['email_opened_at'].isoformat() if row['email_opened_at'] else None,
            'open_count': row['email_open_count'],
            'bounced_at': row['email_bounced_at'].isoformat() if row['email_bounced_at'] else None,
            'rejected_at': row['email_rejected_at'].isoformat() if row['email_rejected_at'] else None,
            'clicks': clicks,
            'has_tracking': row['email_tracking_id'] is not None,
        }
    except Exception as exc:
        logger.error(
            "Failed to get email stats",
            reply_id=reply_id,
            error=str(exc),
        )
        return None
