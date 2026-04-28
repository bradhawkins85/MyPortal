"""API endpoints for email tracking.

Provides endpoints for:
- Serving tracking pixels (1x1 transparent GIF)
- Handling link click redirects
- Recording tracking events
- Listing per-recipient delivery status for ticket reply emails
"""

from __future__ import annotations

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from loguru import logger

from app.api.dependencies.auth import get_current_user
from app.repositories import tickets as tickets_repo
from app.services import email_recipients, email_tracking

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
async def tracking_status(
    tracking_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
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


def _iso(value: object) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:  # pragma: no cover - defensive
            return None
    return str(value)


async def _user_can_view_ticket(ticket: dict, current_user: dict) -> bool:
    """Return True when the caller may view the given ticket.

    Mirrors the access logic used by ``_build_ticket_detail`` in the tickets
    router: helpdesk staff (super admins or members with the helpdesk
    permission) see every ticket; everyone else may only see tickets they
    are the requester for or are watching.
    """
    # Avoid an import cycle with app.api.routes.tickets at module load time.
    from app.api.routes.tickets import _has_helpdesk_permission

    if await _has_helpdesk_permission(current_user):
        return True

    requester_id = ticket.get("requester_id")
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id_int = None

    if user_id_int is not None and requester_id == user_id_int:
        return True

    if user_id_int is None:
        return False

    try:
        from app.repositories import tickets as _tickets_repo

        return bool(await _tickets_repo.is_ticket_watcher(int(ticket["id"]), user_id_int))
    except Exception:  # pragma: no cover - defensive
        return False


@router.get(
    "/replies/{reply_id}/recipients",
    summary="List per-recipient delivery status for a ticket reply email",
)
async def list_reply_recipients(
    reply_id: int,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return per-recipient email delivery information for a ticket reply.

    The single delivery-status badge on a ticket reply only reflects the
    aggregate status. This endpoint powers the click-through popup that
    breaks delivery down per recipient (To/CC/BCC), including SMTP2Go
    delivered / opened / bounced events when available.

    The caller must be authenticated and must have access to the ticket
    that the reply belongs to.
    """
    reply = await tickets_repo.get_reply_by_id(reply_id)
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")

    ticket_id = reply.get("ticket_id")
    if ticket_id is None:
        raise HTTPException(status_code=404, detail="Reply not found")

    ticket = await tickets_repo.get_ticket(int(ticket_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Reply not found")

    if not await _user_can_view_ticket(dict(ticket), current_user):
        # Don't leak ticket existence to unauthorised callers.
        raise HTTPException(status_code=404, detail="Reply not found")

    rows = await email_recipients.get_recipients_for_reply(reply_id)

    formatted: list[dict[str, object]] = []
    for row in rows:
        try:
            open_count = int(row.get("email_open_count") or 0)
        except (TypeError, ValueError):
            open_count = 0
        formatted.append(
            {
                "id": row.get("id"),
                "recipient_email": row.get("recipient_email"),
                "recipient_name": row.get("recipient_name"),
                "recipient_role": row.get("recipient_role") or "to",
                "status": email_recipients.compute_status(row),
                "sent_at": _iso(row.get("email_sent_at")),
                "processed_at": _iso(row.get("email_processed_at")),
                "delivered_at": _iso(row.get("email_delivered_at")),
                "opened_at": _iso(row.get("email_opened_at")),
                "open_count": open_count,
                "bounced_at": _iso(row.get("email_bounced_at")),
                "rejected_at": _iso(row.get("email_rejected_at")),
                "spam_at": _iso(row.get("email_spam_at")),
                "last_event_at": _iso(row.get("last_event_at")),
                "last_event_type": row.get("last_event_type"),
                "last_event_detail": row.get("last_event_detail"),
            }
        )

    return {
        "reply_id": reply_id,
        "ticket_id": int(ticket_id),
        "recipient_count": len(formatted),
        "recipients": formatted,
    }
