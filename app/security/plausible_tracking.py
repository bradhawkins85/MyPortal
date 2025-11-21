"""Plausible Analytics tracking middleware for MyPortal application.

This middleware automatically inserts Plausible Analytics tracking code
into HTML responses when the Plausible module is enabled.
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse
from loguru import logger


class PlausibleTrackingMiddleware(BaseHTTPMiddleware):
    """Automatically inject Plausible Analytics tracking code into HTML responses.
    
    This middleware checks if the Plausible integration module is enabled and
    configured, then injects the analytics script into HTML responses before
    the closing </head> tag.
    
    The middleware respects the module's configuration including:
    - base_url: The Plausible instance URL
    - site_domain: The domain to track
    - enabled: Whether the module is active
    """

    def __init__(
        self,
        app,
        *,
        exempt_paths: Iterable[str] | None = None,
        get_plausible_config: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__(app)
        self.exempt_paths = tuple(exempt_paths or ())
        self._get_plausible_config = get_plausible_config

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        
        # Skip injection for exempted paths (e.g., static files, API endpoints)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return response

        # Only process HTML responses
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            return response

        # Skip if response is a streaming response
        if isinstance(response, StreamingResponse):
            return response

        # Get Plausible configuration
        plausible_config = {}
        if self._get_plausible_config:
            try:
                plausible_config = await self._get_plausible_config()
            except Exception as exc:
                logger.error("Failed to get Plausible configuration", error=str(exc))
                return response

        # Check if Plausible is enabled and configured
        if not plausible_config.get("enabled"):
            return response

        base_url = plausible_config.get("base_url", "").strip().rstrip("/")
        site_domain = plausible_config.get("site_domain", "").strip()

        if not base_url or not site_domain:
            return response

        # Validate configuration values to prevent XSS
        if not self._is_safe_url(base_url) or not self._is_safe_domain(site_domain):
            logger.warning(
                "Invalid Plausible configuration detected",
                base_url=base_url,
                site_domain=site_domain,
            )
            return response

        # Read response body
        try:
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            body = body_bytes.decode("utf-8")
        except Exception as exc:
            logger.error("Failed to read response body for Plausible injection", error=str(exc))
            return response

        # Inject Plausible script before closing </head> tag
        script_tag = self._build_script_tag(base_url, site_domain)
        modified_body = self._inject_script(body, script_tag)

        # Create new response with modified body
        new_response = Response(
            content=modified_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

        return new_response

    def _build_script_tag(self, base_url: str, site_domain: str) -> str:
        """Build the Plausible Analytics script tag.
        
        Args:
            base_url: Base URL of the Plausible instance
            site_domain: Domain to track
            
        Returns:
            HTML script tag string
        """
        # HTML-escape the values to prevent injection
        safe_base_url = self._html_escape(base_url)
        safe_site_domain = self._html_escape(site_domain)
        
        return (
            f'\n    <!-- Plausible Analytics -->\n'
            f'    <script defer data-domain="{safe_site_domain}" '
            f'src="{safe_base_url}/js/script.js"></script>\n'
        )

    def _inject_script(self, html: str, script_tag: str) -> str:
        """Inject script tag into HTML before closing </head> tag.
        
        If </head> tag is not found, the script is not injected to avoid
        malformed HTML.
        
        Args:
            html: Original HTML content
            script_tag: Script tag to inject
            
        Returns:
            Modified HTML with script injected
        """
        # Find closing </head> tag (case-insensitive)
        head_match = re.search(r'</head>', html, re.IGNORECASE)
        
        if not head_match:
            # No </head> tag found, don't inject
            return html
        
        # Insert script before </head>
        insert_pos = head_match.start()
        return html[:insert_pos] + script_tag + html[insert_pos:]

    def _is_safe_url(self, url: str) -> bool:
        """Check if URL is safe to use in script src attribute.
        
        Args:
            url: URL to validate
            
        Returns:
            True if URL is safe, False otherwise
        """
        if not url or not isinstance(url, str):
            return False
        
        # Must be HTTP or HTTPS (HTTPS is strongly recommended)
        if not url.startswith("https://") and not url.startswith("http://"):
            return False
        
        # Must not contain quotes or angle brackets that could break HTML
        if any(char in url for char in ['"', "'", '<', '>', '\n', '\r', '\t']):
            return False
        
        return True

    def _is_safe_domain(self, domain: str) -> bool:
        """Check if domain is safe to use in data-domain attribute.
        
        Args:
            domain: Domain to validate
            
        Returns:
            True if domain is safe, False otherwise
        """
        if not domain or not isinstance(domain, str):
            return False
        
        # Must not contain quotes or angle brackets that could break HTML
        if any(char in domain for char in ['"', "'", '<', '>', '\n', '\r', '\t']):
            return False
        
        # Should match domain pattern (alphanumeric, dots, hyphens, optional port)
        if not re.match(r'^[A-Za-z0-9._-]+(?::\d+)?$', domain):
            return False
        
        return True

    def _html_escape(self, text: str) -> str:
        """Escape text for use in HTML attributes.
        
        Args:
            text: Text to escape
            
        Returns:
            HTML-escaped text
        """
        return (
            text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#x27;')
        )
