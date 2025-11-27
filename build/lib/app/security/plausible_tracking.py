"""Plausible Analytics tracking middleware for authenticated users.

This middleware sends custom pageview events to Plausible Analytics for
authenticated users, using privacy-conscious practices:
- User identifiers are hashed with HMAC using a secret pepper
- Raw usernames are never sent to Plausible cloud instances
- Events are only sent when explicitly enabled via configuration
"""

from __future__ import annotations

import hmac
import os
import hashlib
from typing import Callable

import httpx
from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Any

from app.core.config import get_settings
from app.security.session import session_manager


# Default pepper warning - should be configured via PLAUSIBLE_PEPPER
_DEFAULT_PEPPER_WARNING = "default-pepper-change-me"


def hash_user_id_for_plausible(user_id: int, pepper: str | None, send_pii: bool = False) -> str:
    """Hash a user ID for Plausible analytics with privacy protections.
    
    Args:
        user_id: The user ID to hash
        pepper: Secret pepper for HMAC hashing (None = use default with warning)
        send_pii: If True, send raw user ID (only for self-hosted, compliant instances)
        
    Returns:
        Hashed or raw user identifier string
    """
    import hashlib
    import hmac
    
    if send_pii:
        # Only for self-hosted, compliant instances
        return f"user_{user_id}"
    
    # Use HMAC hashing for privacy
    if not pepper:
        env_pepper = os.getenv("PLAUSIBLE_PEPPER", "").strip()
        if env_pepper:
            pepper = env_pepper
        else:
            logger.warning(
                "Plausible tracking using default pepper - configure PLAUSIBLE_PEPPER for security"
            )
            pepper = _DEFAULT_PEPPER_WARNING
    
    user_data = str(user_id).encode("utf-8")
    pepper_bytes = pepper.encode("utf-8")
    h = hmac.new(pepper_bytes, user_data, hashlib.sha256)
    
    return f"hash_{h.hexdigest()[:16]}"


class PlausibleTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware to send authenticated pageview events to Plausible Analytics.
    
    This middleware:
    - Tracks pageviews for authenticated users only
    - Hashes user identifiers with HMAC for privacy
    - Sends custom events to Plausible /api/event endpoint
    - Respects PLAUSIBLE_SEND_PII configuration flag
    """

    def __init__(
        self,
        app,
        *,
        exempt_paths: tuple[str, ...] = (),
        get_module_settings: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the middleware.
        
        Args:
            app: The ASGI application
            exempt_paths: Tuple of path prefixes to exclude from tracking
            get_module_settings: Optional callable to get Plausible module settings
        """
        super().__init__(app)
        self.exempt_paths = exempt_paths
        self.get_module_settings = get_module_settings

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process the request and send tracking event if applicable."""
        # Process the request first
        response = await call_next(request)
        
        # Skip tracking for exempt paths (e.g., static files, health checks, API endpoints)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return response
        
        # Skip tracking for non-GET requests (only track pageviews)
        if request.method != "GET":
            return response
        
        # Skip tracking for non-successful responses
        if response.status_code >= 400:
            return response
        
        # Only track authenticated users
        session = await session_manager.load_session(request)
        if not session or not session.user_id:
            return response
        
        # Send tracking event asynchronously (don't block response)
        try:
            await self._send_pageview_event(request, session.user_id)
        except Exception as exc:
            # Log errors but don't fail the request
            logger.debug(
                "Failed to send Plausible tracking event",
                error=str(exc),
                path=path,
            )
        
        return response

    async def _send_pageview_event(self, request: Request, user_id: int) -> None:
        """Send a custom pageview event to Plausible.
        
        Args:
            request: The incoming request
            user_id: The authenticated user ID
        """
        settings = get_settings()
        
        # Get Plausible module configuration
        module_settings = {}
        if self.get_module_settings:
            try:
                module_settings = self.get_module_settings() or {}
            except Exception:
                # If we can't get module settings, skip tracking
                return
        
        # Check if module is enabled
        if not module_settings.get("enabled"):
            return
        
        plausible_settings = module_settings.get("settings") or {}
        
        # Get configuration
        base_url = str(plausible_settings.get("base_url") or "").strip().rstrip("/")
        site_domain = str(plausible_settings.get("site_domain") or "").strip()
        api_key = str(plausible_settings.get("api_key") or "").strip()
        pepper = str(plausible_settings.get("pepper") or "").strip()
        send_pii = bool(plausible_settings.get("send_pii"))
        
        # Validate required configuration
        if not base_url or not site_domain:
            return
        
        # Hash the user ID for privacy (unless explicitly configured to send PII)
        if send_pii:
            # Only allow PII if self-hosted and explicitly configured
            user_identifier = f"user_{user_id}"
        else:
            # Hash user ID with HMAC for privacy
            user_identifier = hash_user_id_for_plausible(user_id, pepper, send_pii)
        
        # Build the event URL
        full_url = str(request.url)
        
        # Prepare event data
        event_data = {
            "domain": site_domain,
            "name": "pageview",
            "url": full_url,
            "props": {
                "user_id": user_identifier,
            },
        }
        
        # Get client information
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        else:
            client = request.client
            client_ip = client.host if client else None
        
        user_agent = request.headers.get("user-agent")
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        # Send the event to Plausible
        api_url = f"{base_url}/api/event"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(api_url, json=event_data, headers=headers)
                response.raise_for_status()
            
            logger.debug(
                "Sent pageview event to Plausible",
                user_id=user_id,
                url=full_url,
                hashed_id=user_identifier if not send_pii else "[redacted]",
            )
        except httpx.HTTPError as exc:
            logger.debug(
                "Failed to send pageview event to Plausible",
                error=str(exc),
                url=full_url,
            )
        except Exception as exc:
            logger.debug(
                "Unexpected error sending pageview event to Plausible",
                error=str(exc),
                url=full_url,
            )

    # Remove the old _hash_user_id method since we now use the shared utility
