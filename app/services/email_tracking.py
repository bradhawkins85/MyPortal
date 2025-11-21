"""Email tracking service for Plausible Analytics integration.

Provides functionality for:
- Generating unique tracking IDs for emails
- Inserting tracking pixels into email HTML
- Rewriting links for click tracking
- Recording tracking events
- Sending events to Plausible Analytics
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from loguru import logger

from app.core.config import get_settings
from app.core.database import db


def generate_tracking_id() -> str:
    """Generate a unique tracking ID for email tracking.
    
    Returns a URL-safe random token.
    """
    return secrets.token_urlsafe(32)


def insert_tracking_pixel(html_body: str, tracking_id: str) -> str:
    """Insert a tracking pixel at the end of the email HTML body.
    
    Args:
        html_body: The HTML content of the email
        tracking_id: Unique tracking ID for this email
        
    Returns:
        Modified HTML with tracking pixel inserted
    """
    settings = get_settings()
    if not settings.portal_url:
        logger.warning("portal_url not configured, cannot insert tracking pixel")
        return html_body
    portal_url = settings.portal_url.rstrip('/')
    pixel_url = f"{portal_url}/api/email-tracking/pixel/{tracking_id}.gif"
    
    # Insert tracking pixel before closing </body> tag if present
    # Otherwise, append to end of HTML
    pixel_html = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none;border:0;"/>'
    
    if '</body>' in html_body.lower():
        # Insert before closing body tag
        body_pos = html_body.lower().rfind('</body>')
        return html_body[:body_pos] + pixel_html + html_body[body_pos:]
    else:
        # Append to end
        return html_body + pixel_html


def rewrite_links_for_tracking(html_body: str, tracking_id: str) -> str:
    """Rewrite all links in email HTML to route through tracking endpoint.
    
    Args:
        html_body: The HTML content of the email
        tracking_id: Unique tracking ID for this email
        
    Returns:
        Modified HTML with rewritten links
    """
    settings = get_settings()
    if not settings.portal_url:
        logger.warning("portal_url not configured, cannot rewrite links for tracking")
        return html_body
    portal_url = settings.portal_url.rstrip('/')
    
    # Pattern to match href attributes in anchor tags
    # Matches: href="..." or href='...'
    href_pattern = re.compile(r'href=(["\'])(https?://[^"\']+)\1', re.IGNORECASE)
    
    def replace_link(match: re.Match[str]) -> str:
        quote = match.group(1)
        original_url = match.group(2)
        
        # Don't rewrite tracking pixel URLs or our own portal URLs
        if 'email-tracking/pixel' in original_url or original_url.startswith(portal_url):
            return match.group(0)
        
        # Create tracking redirect URL
        params = urlencode({
            'tid': tracking_id,
            'url': original_url
        })
        tracking_url = f"{portal_url}/api/email-tracking/click?{params}"
        
        return f'href={quote}{tracking_url}{quote}'
    
    return href_pattern.sub(replace_link, html_body)


async def record_email_sent(
    ticket_reply_id: int,
    tracking_id: str,
) -> None:
    """Record that an email was sent with tracking enabled.
    
    Args:
        ticket_reply_id: ID of the ticket reply that was sent
        tracking_id: Unique tracking ID for this email
    """
    query = """
        UPDATE ticket_replies
        SET email_tracking_id = :tracking_id,
            email_sent_at = :sent_at
        WHERE id = :reply_id
    """
    params = {
        'tracking_id': tracking_id,
        'sent_at': datetime.now(timezone.utc),
        'reply_id': ticket_reply_id,
    }
    
    try:
        await db.execute(query, params)
        logger.info(
            "Recorded email tracking metadata",
            reply_id=ticket_reply_id,
            tracking_id=tracking_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to record email tracking metadata",
            reply_id=ticket_reply_id,
            tracking_id=tracking_id,
            error=str(exc),
        )


async def record_tracking_event(
    tracking_id: str,
    event_type: str,
    event_url: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
    referrer: str | None = None,
) -> dict[str, Any] | None:
    """Record an email tracking event (open or click).
    
    Args:
        tracking_id: Tracking ID from the email
        event_type: Type of event ('open' or 'click')
        event_url: URL that was clicked (for click events)
        user_agent: User agent from the request
        ip_address: IP address from the request
        referrer: Referrer from the request
        
    Returns:
        Event record as a dict, or None if recording failed
    """
    # Insert tracking event
    insert_query = """
        INSERT INTO email_tracking_events
        (tracking_id, event_type, event_url, user_agent, ip_address, referrer, occurred_at)
        VALUES (:tracking_id, :event_type, :event_url, :user_agent, :ip_address, :referrer, :occurred_at)
    """
    insert_params = {
        'tracking_id': tracking_id,
        'event_type': event_type,
        'event_url': event_url,
        'user_agent': user_agent,
        'ip_address': ip_address,
        'referrer': referrer,
        'occurred_at': datetime.now(timezone.utc),
    }
    
    try:
        event_id = await db.execute(insert_query, insert_params)
        
        # If this is an open event, update the ticket_replies table
        if event_type == 'open':
            update_query = """
                UPDATE ticket_replies
                SET email_opened_at = COALESCE(email_opened_at, :occurred_at),
                    email_open_count = email_open_count + 1
                WHERE email_tracking_id = :tracking_id
            """
            update_params = {
                'occurred_at': datetime.now(timezone.utc),
                'tracking_id': tracking_id,
            }
            await db.execute(update_query, update_params)
        
        logger.info(
            "Recorded tracking event",
            event_id=event_id,
            tracking_id=tracking_id,
            event_type=event_type,
        )
        
        # Return the event record
        return {
            'id': event_id,
            'tracking_id': tracking_id,
            'event_type': event_type,
            'event_url': event_url,
            'occurred_at': datetime.now(timezone.utc),
        }
        
    except Exception as exc:
        logger.error(
            "Failed to record tracking event",
            tracking_id=tracking_id,
            event_type=event_type,
            error=str(exc),
        )
        return None


async def get_tracking_status(tracking_id: str) -> dict[str, Any] | None:
    """Get tracking status for an email.
    
    Args:
        tracking_id: Tracking ID from the email
        
    Returns:
        Dict with tracking status, or None if not found
    """
    query = """
        SELECT 
            email_tracking_id,
            email_sent_at,
            email_opened_at,
            email_open_count
        FROM ticket_replies
        WHERE email_tracking_id = :tracking_id
        LIMIT 1
    """
    params = {'tracking_id': tracking_id}
    
    try:
        row = await db.fetch_one(query, params)
        if not row:
            return None
        
        return {
            'tracking_id': row['email_tracking_id'],
            'sent_at': row['email_sent_at'],
            'opened_at': row['email_opened_at'],
            'open_count': row['email_open_count'],
            'is_opened': row['email_opened_at'] is not None,
        }
    except Exception as exc:
        logger.error(
            "Failed to get tracking status",
            tracking_id=tracking_id,
            error=str(exc),
        )
        return None


async def get_reply_tracking_status(reply_id: int) -> dict[str, Any] | None:
    """Get tracking status for a ticket reply.
    
    Args:
        reply_id: ID of the ticket reply
        
    Returns:
        Dict with tracking status, or None if not found
    """
    query = """
        SELECT 
            email_tracking_id,
            email_sent_at,
            email_opened_at,
            email_open_count
        FROM ticket_replies
        WHERE id = :reply_id
        LIMIT 1
    """
    params = {'reply_id': reply_id}
    
    try:
        row = await db.fetch_one(query, params)
        if not row:
            return None
        
        return {
            'tracking_id': row['email_tracking_id'],
            'sent_at': row['email_sent_at'],
            'opened_at': row['email_opened_at'],
            'open_count': row['email_open_count'],
            'is_opened': row['email_opened_at'] is not None,
            'has_tracking': row['email_tracking_id'] is not None,
        }
    except Exception as exc:
        logger.error(
            "Failed to get reply tracking status",
            reply_id=reply_id,
            error=str(exc),
        )
        return None


async def send_event_to_plausible(
    event_type: str,
    tracking_id: str,
    event_url: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> bool:
    """Send a tracking event to Plausible Analytics.
    
    Args:
        event_type: Type of event ('open' or 'click')
        tracking_id: Tracking ID from the email
        event_url: URL that was clicked (for click events)
        user_agent: User agent from the request
        ip_address: IP address from the request
        
    Returns:
        True if event was sent successfully, False otherwise
    """
    # Import httpx here to avoid circular imports
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not available, cannot send events to Plausible")
        return False
    
    # Get Plausible configuration from integration module
    from app.services import modules as modules_service
    
    try:
        module_settings = await modules_service.get_module_settings('plausible')
        if not module_settings or not module_settings.get('send_to_plausible'):
            # Plausible integration not configured or disabled
            return False
        
        base_url = module_settings.get('base_url', '').rstrip('/')
        site_domain = module_settings.get('site_domain', '')
        
        if not base_url or not site_domain:
            logger.warning("Plausible base_url or site_domain not configured")
            return False
        
        settings = get_settings()
        if not settings.portal_url:
            logger.warning("portal_url not configured, cannot send event to Plausible")
            return False
        portal_url = settings.portal_url.rstrip('/')
        
        # Build event data
        event_name = f"email_{event_type}"
        event_data = {
            'domain': site_domain,
            'name': event_name,
            'url': event_url or f"{portal_url}/email-tracking/{tracking_id}",
            'props': {
                'tracking_id': tracking_id,
                'event_type': event_type,
            }
        }
        
        # Send to Plausible
        api_url = f"{base_url}/api/event"
        headers = {
            'Content-Type': 'application/json',
        }
        if user_agent:
            headers['User-Agent'] = user_agent
        if ip_address:
            headers['X-Forwarded-For'] = ip_address
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(api_url, json=event_data, headers=headers)
            response.raise_for_status()
        
        logger.info(
            "Sent tracking event to Plausible",
            tracking_id=tracking_id,
            event_type=event_type,
        )
        return True
        
    except Exception as exc:
        logger.error(
            "Failed to send tracking event to Plausible",
            tracking_id=tracking_id,
            event_type=event_type,
            error=str(exc),
        )
        return False
