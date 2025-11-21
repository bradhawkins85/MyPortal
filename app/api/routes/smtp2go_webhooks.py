"""API endpoints for SMTP2Go webhook events.

Handles webhook callbacks from SMTP2Go for:
- Email delivery notifications
- Email open tracking
- Link click tracking
- Bounce and spam reports
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, Body, Header, HTTPException, Request
from loguru import logger

from app.services import smtp2go

router = APIRouter(prefix="/api/webhooks/smtp2go", tags=["SMTP2Go Webhooks"])


async def verify_webhook_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    """Verify SMTP2Go webhook signature.
    
    Args:
        payload: Raw request body bytes
        signature: Signature from X-Smtp2go-Signature header
        secret: Webhook secret from configuration
        
    Returns:
        True if signature is valid, False otherwise
    """
    if not signature or not secret:
        return False
    
    # SMTP2Go uses HMAC-SHA256 for webhook signatures
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)


@router.post("/events", include_in_schema=False)
async def smtp2go_webhook(
    request: Request,
    event: Annotated[dict, Body()],
    x_smtp2go_signature: Annotated[str | None, Header()] = None,
) -> dict:
    """Handle webhook events from SMTP2Go.
    
    SMTP2Go sends webhook events for various email lifecycle events:
    - delivered: Email successfully delivered to recipient
    - opened: Recipient opened the email
    - clicked: Recipient clicked a link in the email
    - bounced: Email bounced (hard or soft bounce)
    - spam: Email marked as spam
    
    Args:
        request: FastAPI request object
        event: Single event object from SMTP2Go
        x_smtp2go_signature: Webhook signature for verification
        
    Returns:
        Success response
    """
    # Get webhook secret from configuration
    from app.services import modules as modules_service
    
    try:
        module_settings = await modules_service.get_module_settings('smtp2go')
        webhook_secret = module_settings.get('webhook_secret') if module_settings else None
        
        # Verify webhook signature if secret is configured
        if webhook_secret:
            body = await request.body()
            if not await verify_webhook_signature(body, x_smtp2go_signature, webhook_secret):
                logger.warning(
                    "SMTP2Go webhook signature verification failed",
                    has_signature=x_smtp2go_signature is not None,
                )
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
        else:
            logger.info("SMTP2Go webhook received without signature verification (secret not configured)")
        
    except Exception as exc:
        logger.error(
            "Failed to verify SMTP2Go webhook",
            error=str(exc),
        )
        # Continue processing even if verification fails to avoid losing events
    
    # Process the event
    event_type = event.get('event')
    
    try:
        result = await smtp2go.process_webhook_event(event_type, event)
        if result:
            logger.info(
                "SMTP2Go webhook event processed successfully",
                event_type=event_type,
            )
            return {
                "status": "ok",
                "processed": 1,
                "failed": 0,
            }
        else:
            logger.warning(
                "Failed to process SMTP2Go webhook event",
                event_type=event_type,
                event_data=event,
            )
            return {
                "status": "ok",
                "processed": 0,
                "failed": 1,
            }
    except Exception as exc:
        logger.error(
            "Error processing SMTP2Go webhook event",
            event_type=event_type,
            error=str(exc),
        )
        return {
            "status": "ok",
            "processed": 0,
            "failed": 1,
        }
