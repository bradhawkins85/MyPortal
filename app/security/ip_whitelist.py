"""IP Whitelisting middleware for MyPortal application.

This middleware provides IP-based access control for sensitive endpoints.
It can be configured to restrict access to admin routes and API endpoints
to specific IP addresses or CIDR ranges.
"""
from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address, IPv6Address, ip_address, ip_network
from typing import Iterable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings


class IPWhitelistMiddleware:
    """Restrict access to specific endpoints based on client IP address.
    
    This middleware checks the client's IP address against a whitelist of
    allowed IP addresses or CIDR ranges. If the IP is not in the whitelist,
    access is denied with a 403 Forbidden response.
    
    The middleware supports:
    - Individual IPv4 and IPv6 addresses
    - CIDR ranges (e.g., 192.168.1.0/24, 2001:db8::/32)
    - Path-based exemptions (e.g., public endpoints)
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        whitelist: Iterable[str] | None = None,
        protected_paths: Iterable[str] | None = None,
        exempt_paths: Iterable[str] | None = None,
        enabled: bool = True,
    ) -> None:
        """Initialize the IP whitelist middleware.
        
        Args:
            app: The ASGI application
            whitelist: List of allowed IP addresses or CIDR ranges
            protected_paths: List of path prefixes to protect (e.g., ["/admin", "/api"])
            exempt_paths: List of path prefixes to exempt from IP checking
            enabled: Whether IP whitelisting is enabled
        """
        self.app = app
        self._settings = get_settings()
        self._enabled = enabled
        self._protected_paths = tuple(protected_paths or [])
        self._exempt_paths = tuple(exempt_paths or [])
        
        # Parse and validate whitelist
        self._whitelist: list[IPv4Address | IPv6Address | object] = []
        if whitelist:
            for entry in whitelist:
                entry_str = str(entry).strip()
                if not entry_str:
                    continue
                try:
                    # Try parsing as network (supports both single IPs and CIDR)
                    network = ip_network(entry_str, strict=False)
                    self._whitelist.append(network)
                except (ValueError, AddressValueError) as exc:
                    logger.warning(
                        "Invalid IP whitelist entry - skipping",
                        entry=entry_str,
                        error=str(exc),
                    )

    def _get_client_ip(self, request: Request) -> str | None:
        """Extract the client's IP address from the request socket.

        The whitelist check must use the direct peer address. Proxy headers
        (for example ``CF-Connecting-IP`` and ``X-Forwarded-For``) are not
        trusted here because clients can spoof them when connecting directly.

        Args:
            request: The incoming request

        Returns:
            The client's IP address as a string, or None if unavailable
        """
        if request.client:
            return request.client.host

        return None

    def _is_ip_allowed(self, client_ip_str: str) -> bool:
        """Check if the client IP is in the whitelist.
        
        Args:
            client_ip_str: The client's IP address as a string
            
        Returns:
            True if the IP is allowed, False otherwise
        """
        if not self._whitelist:
            # No whitelist configured = allow all
            return True
        
        try:
            client_ip = ip_address(client_ip_str)
        except (ValueError, AddressValueError):
            logger.warning("Invalid client IP address", ip=client_ip_str)
            return False
        
        # Check if client IP is in any whitelisted network/range
        # Both single IPs and CIDR ranges are stored as ip_network objects
        # which support the 'in' operator
        for network in self._whitelist:
            if client_ip in network:  # type: ignore[operator]
                return True
        
        return False

    def _should_check_ip(self, path: str) -> bool:
        """Determine if IP checking should be applied to this path.
        
        Args:
            path: The request path
            
        Returns:
            True if IP checking should be applied, False otherwise
        """
        # Check exempt paths first
        if any(path.startswith(prefix) for prefix in self._exempt_paths):
            return False
        
        # If protected paths are specified, only check those
        if self._protected_paths:
            return any(path.startswith(prefix) for prefix in self._protected_paths)
        
        # No protected paths specified = check all paths
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply IP whitelisting, answering 403 for requests from other addresses."""
        # Skip if disabled
        if scope["type"] != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        # Check if this path should be protected
        if not self._should_check_ip(path):
            await self.app(scope, receive, send)
            return
        
        # Extract client IP
        client_ip = self._get_client_ip(request)
        if not client_ip:
            logger.warning(
                "IP whitelist check failed - could not determine client IP",
                path=path,
                method=request.method,
            )
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied - IP verification failed"},
            )
            await response(scope, receive, send)
            return
        
        # Check whitelist
        if not self._is_ip_allowed(client_ip):
            logger.warning(
                "IP whitelist check failed - IP not in whitelist",
                client_ip=client_ip,
                path=path,
                method=request.method,
            )
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied - IP address not authorized"},
            )
            await response(scope, receive, send)
            return

        # IP is allowed, continue to next handler
        await self.app(scope, receive, send)
