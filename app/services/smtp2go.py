"""SMTP2Go API service for enhanced email delivery and tracking.

Provides functionality for:
- Sending emails via SMTP2Go API
- Processing webhooks for delivery, open, and click events
- Tracking email status and events
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from app.core.config import get_settings
from app.core.database import db


class SMTP2GoError(Exception):
    """Raised when SMTP2Go API request fails."""


def generate_tracking_id() -> str:
    """Generate a unique tracking ID for email tracking.
    
    Returns a URL-safe random token.
    """
    return secrets.token_urlsafe(32)


async def send_email_via_api(
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    custom_headers: dict[str, str] | None = None,
    tracking_id: str | None = None,
) -> dict[str, Any]:
    """Send an email using SMTP2Go API.
    
    Args:
        to: List of recipient email addresses
        subject: Email subject line
        html_body: HTML content of the email
        text_body: Plain text version of the email (optional)
        sender: Sender email address (optional)
        reply_to: Reply-to email address (optional)
        custom_headers: Additional email headers (optional)
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
        
        # Build request payload
        payload = {
            "api_key": api_key,
            "to": to,
            "subject": subject,
            "html_body": html_body,
        }
        
        # Add optional fields
        if text_body:
            payload["text_body"] = text_body
        
        if sender:
            payload["sender"] = sender
        elif settings.smtp_user:
            payload["sender"] = settings.smtp_user
        
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
            response.raise_for_status()
            result = response.json()
        
        # Check if request was successful
        if result.get("data", {}).get("error_code") != "SUCCESS":
            error_msg = result.get("data", {}).get("error", "Unknown error")
            raise SMTP2GoError(f"SMTP2Go API error: {error_msg}")
        
        logger.info(
            "Email sent via SMTP2Go API",
            subject=subject,
            recipients=to,
            message_id=result.get("data", {}).get("email_id"),
            tracking_id=tracking_id,
        )
        
        return result.get("data", {})
        
    except httpx.HTTPError as exc:
        logger.error(
            "SMTP2Go API request failed",
            subject=subject,
            recipients=to,
            error=str(exc),
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
    event_type: str,
    event_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Process a webhook event from SMTP2Go.
    
    Args:
        event_type: Type of event (delivered, opened, clicked, bounced, etc.)
        event_data: Event payload from SMTP2Go
        
    Returns:
        Event record as a dict, or None if processing failed
    """
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
        
        if event_type in ['delivered', 'delivery']:
            internal_event_type = 'delivered'
        elif event_type in ['opened', 'open']:
            internal_event_type = 'open'
        elif event_type in ['clicked', 'click']:
            internal_event_type = 'click'
            event_url = event_data.get('url')
        elif event_type in ['bounced', 'bounce']:
            internal_event_type = 'bounce'
        elif event_type in ['spam', 'spam_complaint']:
            internal_event_type = 'spam'
        
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
        if internal_event_type == 'delivered':
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
            email_delivered_at,
            email_opened_at,
            email_open_count,
            email_bounced_at
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
            'delivered_at': row['email_delivered_at'].isoformat() if row['email_delivered_at'] else None,
            'opened_at': row['email_opened_at'].isoformat() if row['email_opened_at'] else None,
            'open_count': row['email_open_count'],
            'bounced_at': row['email_bounced_at'].isoformat() if row['email_bounced_at'] else None,
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
