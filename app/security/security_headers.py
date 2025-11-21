"""Security headers middleware for MyPortal application.

This middleware adds security headers to all HTTP responses to protect against
common web vulnerabilities including XSS, clickjacking, and MIME-sniffing attacks.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable, Iterable
from urllib.parse import urlparse

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import get_settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses.
    
    Headers added:
    - Content-Security-Policy: Restrict resource loading to same origin
    - X-Frame-Options: Prevent clickjacking attacks
    - X-Content-Type-Options: Prevent MIME-sniffing attacks
    - Referrer-Policy: Control referrer information leakage
    - Permissions-Policy: Disable sensitive browser features
    - Strict-Transport-Security: Enforce HTTPS connections (when TLS is enabled)
    """

    def __init__(
        self,
        app,
        *,
        exempt_paths: Iterable[str] | None = None,
        get_extra_script_sources: Callable[[], Awaitable[list[str]]] | None = None,
        get_extra_connect_sources: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> None:
        super().__init__(app)
        self.exempt_paths = tuple(exempt_paths or ())
        self._settings = get_settings()
        self._get_extra_script_sources = get_extra_script_sources
        self._get_extra_connect_sources = get_extra_connect_sources

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        
        # Skip security headers for exempted paths (e.g., static files)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return response

        # Build script-src directive with dynamic sources
        script_sources = ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://unpkg.com"]

        # Add extra script sources (e.g., Plausible analytics)
        if self._get_extra_script_sources:
            try:
                extra_sources = await self._get_extra_script_sources()
                for source in extra_sources:
                    if source and self._is_valid_csp_source(source):
                        script_sources.append(source)
            except Exception:
                # If we fail to get extra sources, continue with defaults
                # This ensures CSP is always present even if source lookup fails
                pass

        # Build connect-src directive
        connect_sources = ["'self'"]

        # Add extra connect sources (e.g., analytics APIs)
        if self._get_extra_connect_sources:
            try:
                extra_sources = await self._get_extra_connect_sources()
                for source in extra_sources:
                    if source and self._is_valid_csp_source(source):
                        connect_sources.append(source)
            except Exception:
                # If we fail to get extra sources, continue with defaults
                pass
        
        # Content-Security-Policy: Restrict resource loading to same origin
        # Allow 'unsafe-inline' for styles and scripts that are inline in templates
        # Allow 'unsafe-eval' for some JavaScript libraries that use eval
        # Allow unpkg.com for loading htmx library from CDN
        # In production, these should be replaced with nonces or hashes
        csp_directives = [
            "default-src 'self'",
            f"script-src {' '.join(script_sources)}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            f"connect-src {' '.join(connect_sources)}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # X-Frame-Options: Prevent the page from being embedded in iframes
        response.headers["X-Frame-Options"] = "DENY"

        # X-Content-Type-Options: Prevent MIME-sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Referrer-Policy: Control referrer information sent with requests
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy: Disable sensitive browser features
        permissions = [
            "geolocation=()",
            "microphone=()",
            "camera=()",
            "payment=()",
            "usb=()",
            "magnetometer=()",
            "gyroscope=()",
            "accelerometer=()",
        ]
        response.headers["Permissions-Policy"] = ", ".join(permissions)

        # Strict-Transport-Security: Enforce HTTPS for 1 year (only if TLS is enabled)
        # This header should only be sent over HTTPS connections
        if self._settings.enable_hsts and request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # X-XSS-Protection: Legacy header for older browsers
        # Modern browsers use CSP instead, but this provides defense in depth
        response.headers["X-XSS-Protection"] = "1; mode=block"

        return response

    def _is_valid_csp_source(self, source: str) -> bool:
        """Validate that a CSP source is safe to include.
        
        This prevents CSP injection attacks by ensuring the source:
        - Is a valid HTTPS URL
        - Contains no whitespace or special characters that could break CSP
        - Matches expected URL format
        
        Args:
            source: The CSP source to validate (e.g., "https://example.com")
            
        Returns:
            True if the source is valid and safe, False otherwise
        """
        if not source or not isinstance(source, str):
            return False
        
        # Remove whitespace
        source = source.strip()
        
        # Check for characters that could break CSP or enable injection
        if any(char in source for char in [" ", ";", "'", '"', "\n", "\r", "\t"]):
            return False
        
        # Must be HTTPS URL
        if not source.startswith("https://"):
            return False
        
        try:
            parsed = urlparse(source)
            # Must have a valid netloc (domain)
            if not parsed.netloc:
                return False
            # Domain must be valid (alphanumeric, dots, hyphens, optional port)
            # Note: underscores are not allowed in RFC-compliant domain names
            if not re.match(r"^[a-zA-Z0-9.-]+(?::\d+)?$", parsed.netloc):
                return False
            return True
        except Exception:
            return False
