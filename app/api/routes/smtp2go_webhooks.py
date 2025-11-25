"""API endpoints for SMTP2Go webhook events.

Handles webhook callbacks from SMTP2Go for:
- Email delivery notifications
- Email open tracking
- Link click tracking
- Bounce and spam reports
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger

from app.services import smtp2go

router = APIRouter(prefix="/api/webhooks/smtp2go", tags=["SMTP2Go Webhooks"])


def _parse_timestamp_signature(signature: str) -> tuple[str | None, str | None]:
    """Parse SMTP2Go timestamp-based signature format: t=<timestamp>,v1=<signature>.
    
    Args:
        signature: Raw signature header value
        
    Returns:
        Tuple of (timestamp, signature_value) or (None, None) if not in this format
    """
    parts = {}
    for part in signature.split(','):
        if '=' in part:
            key, value = part.split('=', 1)
            parts[key.strip()] = value.strip()
    
    timestamp = parts.get('t')
    sig_value = parts.get('v1')
    
    if timestamp and sig_value:
        return timestamp, sig_value
    return None, None


async def verify_webhook_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    """Verify SMTP2Go webhook signature.
    
    Supports multiple signature formats:
    1. Timestamp-based: t=<timestamp>,v1=<signature> (SMTP2Go's current format)
       - Signature is HMAC-SHA256(secret, "<timestamp>.<payload>")
    2. Plain hex digest: HMAC-SHA256 of payload only
    3. Base64 encoded: Base64 of HMAC-SHA256 digest
    4. sha256= prefixed: "sha256=<hex_digest>"
    
    Args:
        payload: Raw request body bytes
        signature: Signature from X-Smtp2go-Signature header
        secret: Webhook secret from configuration
        
    Returns:
        True if signature is valid, False otherwise
    """
    if not signature or not secret:
        logger.debug("Signature verification skipped - missing signature or secret")
        return False
    
    signature_to_check = signature.strip()
    
    # Check for SMTP2Go's timestamp-based signature format: t=<timestamp>,v1=<signature>
    # This is the primary format used by SMTP2Go for webhook verification
    timestamp, sig_value = _parse_timestamp_signature(signature_to_check)
    if timestamp and sig_value:
        # SMTP2Go computes: HMAC-SHA256(secret, "<timestamp>.<payload>")
        signed_payload = f"{timestamp}.".encode('utf-8') + payload
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            signed_payload,
            hashlib.sha256
        ).hexdigest()
        
        logger.debug(
            "Verifying timestamp-based signature",
            timestamp=timestamp,
            payload_length=len(payload),
            received_signature_prefix=sig_value[:16] + "..." if len(sig_value) > 16 else sig_value,
            expected_signature_prefix=expected_sig[:16] + "..." if len(expected_sig) > 16 else expected_sig,
        )
        
        # Compare signatures (case-insensitive for hex)
        is_valid = (
            hmac.compare_digest(sig_value.lower(), expected_sig.lower())
            or hmac.compare_digest(sig_value.upper(), expected_sig.upper())
        )
        
        if is_valid:
            return True
        
        # Log mismatch for debugging
        logger.warning(
            "Timestamp-based signature mismatch",
            timestamp=timestamp,
            received_prefix=sig_value[:16] + "...",
            expected_prefix=expected_sig[:16] + "...",
            payload_sample=payload[:200].decode('utf-8', errors='replace') if len(payload) > 0 else "",
        )
        # Fall through to try legacy formats
    
    # Legacy format support: HMAC-SHA256 of payload only (no timestamp)
    # This supports older implementations and testing scenarios
    hmac_bytes = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).digest()
    expected_hex = hmac_bytes.hex()
    expected_base64 = base64.b64encode(hmac_bytes).decode('ascii')
    
    # Some webhook providers prefix their signatures (e.g., "sha256=..."). Only
    # strip a known prefix to avoid corrupting base64 signatures that include
    # padding characters.
    if signature_to_check.lower().startswith("sha256="):
        signature_to_check = signature_to_check.split('=', 1)[1]
        logger.debug(
            "Signature has prefix, extracting",
            prefix="sha256",
            extracted_signature=signature_to_check[:16] + "..."
        )
    
    # Log signature details for debugging
    logger.debug(
        "Webhook signature verification details (legacy format)",
        payload_length=len(payload),
        payload_preview=payload[:100].decode('utf-8', errors='replace') if len(payload) > 0 else "",
        signature_length=len(signature_to_check),
        expected_signature_hex=expected_hex[:16] + "..." if len(expected_hex) > 16 else expected_hex,
        expected_signature_base64=expected_base64[:16] + "..." if len(expected_base64) > 16 else expected_base64,
        received_signature=signature_to_check[:16] + "..." if len(signature_to_check) > 16 else signature_to_check,
    )

    # Compare signatures using constant-time comparison against both supported
    # encodings. Hex digests from SMTP2Go can be upper or lower case, so we
    # normalise when comparing.
    is_valid = (
        hmac.compare_digest(signature_to_check.lower(), expected_hex)
        or hmac.compare_digest(signature_to_check.upper(), expected_hex.upper())
        or hmac.compare_digest(signature_to_check, expected_base64)
    )
    
    if not is_valid:
        # Log truncated signatures to avoid exposing sensitive data
        logger.warning(
            "Signature mismatch (legacy format)",
            expected_hex_prefix=expected_hex[:16] + "...",
            expected_base64_prefix=expected_base64[:16] + "...",
            received_prefix=signature_to_check[:16] + "...",
            payload_sample=payload[:200].decode('utf-8', errors='replace') if len(payload) > 0 else "",
        )
    
    return is_valid


@router.post("/events", include_in_schema=False)
async def smtp2go_webhook(
    request: Request,
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
        x_smtp2go_signature: Webhook signature for verification
        
    Returns:
        Success response
    """
    # Get webhook secret from configuration
    from app.services import modules as modules_service
    from app.services import webhook_monitor
    
    # Capture request details for logging
    source_url = str(request.url)
    request_headers = dict(request.headers)
    
    # Read raw body FIRST before any parsing
    # This ensures we have the exact bytes that SMTP2Go signed
    raw_body = await request.body()
    
    try:
        module_settings = await modules_service.get_module_settings('smtp2go')
        webhook_secret = module_settings.get('webhook_secret') if module_settings else None
        
        # Verify webhook signature if secret is configured
        if webhook_secret:
            if not await verify_webhook_signature(raw_body, x_smtp2go_signature, webhook_secret):
                logger.warning(
                    "SMTP2Go webhook signature verification failed",
                    has_signature=x_smtp2go_signature is not None,
                    signature_preview=x_smtp2go_signature[:16] + "..." if x_smtp2go_signature and len(x_smtp2go_signature) > 16 else x_smtp2go_signature,
                    body_length=len(raw_body),
                )
                # Log the failed verification
                await webhook_monitor.log_incoming_webhook(
                    name="SMTP2Go Webhook - Signature Verification Failed",
                    source_url=source_url,
                    payload=raw_body.decode('utf-8', errors='replace')[:1000],  # Log truncated body
                    headers=request_headers,
                    response_status=401,
                    response_body="Invalid webhook signature",
                    error_message="Signature verification failed",
                )
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
        else:
            logger.info("SMTP2Go webhook received without signature verification (secret not configured)")
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to verify SMTP2Go webhook",
            error=str(exc),
        )
        # Continue processing even if verification fails to avoid losing events
    
    # Parse the JSON body manually now that signature is verified
    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse SMTP2Go webhook JSON", error=str(exc))
        await webhook_monitor.log_incoming_webhook(
            name="SMTP2Go Webhook - Invalid JSON",
            source_url=source_url,
            payload=raw_body.decode('utf-8', errors='replace')[:1000],
            headers=request_headers,
            response_status=400,
            response_body="Invalid JSON payload",
            error_message=str(exc),
        )
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # Normalise payload to a list to support both single events and batched events
    events: list[dict]
    if isinstance(event, list):
        events = [item for item in event if isinstance(item, dict)]
    elif isinstance(event, dict):
        events = [event]
    else:
        await webhook_monitor.log_incoming_webhook(
            name="SMTP2Go Webhook - Invalid Payload",
            source_url=source_url,
            payload=event,
            headers=request_headers,
            response_status=400,
            response_body="Invalid webhook payload",
            error_message="Payload is not a dict or list",
        )
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    processed_count = 0
    error_message = None
    try:
        for event_item in events:
            event_type = event_item.get('event')
            email_id = event_item.get('email_id')

            result = await smtp2go.process_webhook_event(event_type, event_item)
            if result:
                processed_count += 1
                logger.info(
                    "SMTP2Go webhook event processed successfully",
                    event_type=event_type,
                    email_id=email_id,
                    tracking_id=result.get('tracking_id'),
                )
            else:
                logger.warning(
                    "Failed to process SMTP2Go webhook event",
                    event_type=event_type,
                    email_id=email_id,
                    event_data=event_item,
                )

        if processed_count > 0:
            # Log successful webhook processing
            await webhook_monitor.log_incoming_webhook(
                name=f"SMTP2Go Webhook - {processed_count} event(s) processed",
                source_url=source_url,
                payload=event,
                headers=request_headers,
                response_status=200,
                response_body=f"Successfully processed {processed_count} event(s)",
            )
            return {"status": "success", "processed": processed_count}

        error_message = "Event processing failed - unknown email ID or message not tracked"
        await webhook_monitor.log_incoming_webhook(
            name="SMTP2Go Webhook - Processing Failed",
            source_url=source_url,
            payload=event,
            headers=request_headers,
            response_status=200,
            response_body=error_message,
            error_message=error_message,
        )
        return {
            "status": "failed",
            "error": error_message,
        }
    except Exception as exc:
        error_message = str(exc)
        logger.error(
            "Error processing SMTP2Go webhook event",
            error=error_message,
        )
        await webhook_monitor.log_incoming_webhook(
            name="SMTP2Go Webhook - Exception",
            source_url=source_url,
            payload=event,
            headers=request_headers,
            response_status=200,
            response_body=error_message,
            error_message=error_message,
        )
        return {
            "status": "failed",
            "error": error_message,
        }
