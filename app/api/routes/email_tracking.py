"""API endpoints for email tracking.

Provides endpoints for:
- Serving tracking pixels (1x1 transparent GIF)
- Handling link click redirects
- Recording tracking events
"""

from __future__ import annotations

import base64
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from loguru import logger

from app.services import email_tracking

router = APIRouter(prefix="/api/email-tracking", tags=["Email Tracking"])

# 1x1 transparent GIF in base64
# This is a tiny transparent GIF image used as a tracking pixel
TRACKING_PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


@router.get("/pixel/{tracking_id}.gif", include_in_schema=False)
async def tracking_pixel(
    tracking_id: str,
    request: Request,
) -> Response:
    """Serve a 1x1 transparent GIF and record email open event.
    
    This endpoint is called when an email client loads the tracking pixel image.
    It records the open event and returns a transparent GIF.
    
    Args:
        tracking_id: Unique tracking ID from the email
        request: FastAPI request object
        
    Returns:
        Response with 1x1 transparent GIF image
    """
    # Extract request metadata
    user_agent = request.headers.get("user-agent")
    referrer = request.headers.get("referer") or request.headers.get("referrer")
    
    # Get client IP address
    ip_address = None
    if request.client:
        ip_address = request.client.host
    # Check for forwarded IP (if behind proxy)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP if multiple are present
        ip_address = forwarded_for.split(",")[0].strip()
    
    # Record the tracking event asynchronously
    try:
        await email_tracking.record_tracking_event(
            tracking_id=tracking_id,
            event_type='open',
            user_agent=user_agent,
            ip_address=ip_address,
            referrer=referrer,
        )
        
        # Optionally send to Plausible (if configured)
        await email_tracking.send_event_to_plausible(
            event_type='open',
            tracking_id=tracking_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    except Exception as exc:
        # Log error but still return the pixel
        logger.error(
            "Failed to record tracking pixel event",
            tracking_id=tracking_id,
            error=str(exc),
        )
    
    # Return 1x1 transparent GIF
    return Response(
        content=TRACKING_PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@router.get("/click", include_in_schema=False)
async def tracking_click(
    tid: Annotated[str, Query(description="Tracking ID")],
    url: Annotated[str, Query(description="Destination URL")],
    request: Request,
) -> RedirectResponse:
    """Record link click event and redirect to destination URL.
    
    This endpoint is called when a user clicks a tracked link in an email.
    It records the click event and redirects to the original URL.
    
    Args:
        tid: Unique tracking ID from the email
        url: Original destination URL to redirect to
        request: FastAPI request object
        
    Returns:
        Redirect response to the original URL
    """
    # Extract request metadata
    user_agent = request.headers.get("user-agent")
    referrer = request.headers.get("referer") or request.headers.get("referrer")
    
    # Get client IP address
    ip_address = None
    if request.client:
        ip_address = request.client.host
    # Check for forwarded IP (if behind proxy)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP if multiple are present
        ip_address = forwarded_for.split(",")[0].strip()
    
    # Record the tracking event asynchronously
    try:
        await email_tracking.record_tracking_event(
            tracking_id=tid,
            event_type='click',
            event_url=url,
            user_agent=user_agent,
            ip_address=ip_address,
            referrer=referrer,
        )
        
        # Optionally send to Plausible (if configured)
        await email_tracking.send_event_to_plausible(
            event_type='click',
            tracking_id=tid,
            event_url=url,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    except Exception as exc:
        # Log error but still redirect
        logger.error(
            "Failed to record click tracking event",
            tracking_id=tid,
            url=url,
            error=str(exc),
        )
    
    # Redirect to the original URL
    return RedirectResponse(url=url, status_code=302)


@router.get("/status/{tracking_id}")
async def tracking_status(tracking_id: str) -> dict:
    """Get tracking status for an email.
    
    Args:
        tracking_id: Unique tracking ID from the email
        
    Returns:
        Dict with tracking status
    """
    status = await email_tracking.get_tracking_status(tracking_id)
    
    if not status:
        return {
            "tracking_id": tracking_id,
            "found": False,
            "error": "Tracking ID not found"
        }
    
    return {
        "tracking_id": tracking_id,
        "found": True,
        "sent_at": status['sent_at'].isoformat() if status['sent_at'] else None,
        "opened_at": status['opened_at'].isoformat() if status['opened_at'] else None,
        "open_count": status['open_count'],
        "is_opened": status['is_opened'],
    }
