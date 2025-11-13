"""Security headers middleware for MyPortal application.

This middleware adds security headers to all HTTP responses to protect against
common web vulnerabilities including XSS, clickjacking, and MIME-sniffing attacks.
"""
from __future__ import annotations

from typing import Iterable

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
    ) -> None:
        super().__init__(app)
        self.exempt_paths = tuple(exempt_paths or ())
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        
        # Skip security headers for exempted paths (e.g., static files)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return response

        # Content-Security-Policy: Restrict resource loading to same origin
        # Allow 'unsafe-inline' for styles and scripts that are inline in templates
        # Allow 'unsafe-eval' for some JavaScript libraries that use eval
        # In production, these should be replaced with nonces or hashes
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
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
