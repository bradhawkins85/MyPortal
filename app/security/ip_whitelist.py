"""IP Whitelisting middleware for MyPortal application.

This middleware provides IP-based access control for sensitive endpoints.
It can be configured to restrict access to admin routes and API endpoints
to specific IP addresses or CIDR ranges.
"""
from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address, IPv6Address, ip_address, ip_network
from typing import Callable, Iterable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import get_settings


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """Restrict access to specific endpoints based on client IP address.
    
    This middleware checks the client's IP address against a whitelist of
    allowed IP addresses or CIDR ranges. If the IP is not in the whitelist,
    access is denied with a 403 Forbidden response.
    
    The middleware supports:
    - Individual IPv4 and IPv6 addresses
    - CIDR ranges (e.g., 192.168.1.0/24, 2001:db8::/32)
    - Proxy headers (X-Forwarded-For, CF-Connecting-IP)
    - Path-based exemptions (e.g., public endpoints)
    """

    def __init__(
        self,
        app,
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
        super().__init__(app)
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
        """Extract the client's IP address from the request.
        
        Checks proxy headers in priority order:
        1. CF-Connecting-IP (Cloudflare)
        2. X-Forwarded-For (standard proxy header)
        3. Direct client IP from socket
        
        Args:
            request: The incoming request
            
        Returns:
            The client's IP address as a string, or None if unavailable
        """
        # Check Cloudflare header first
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        
        # Check X-Forwarded-For (use first IP in chain)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        # Fall back to direct client IP
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
        
        # Check if client IP is in any whitelisted range
        for allowed in self._whitelist:
            try:
                if client_ip in allowed:  # type: ignore[operator]
                    return True
            except TypeError:
                # Single IP address comparison
                if client_ip == allowed:
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

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process the request and apply IP whitelisting.
        
        Args:
            request: The incoming request
            call_next: The next middleware or endpoint handler
            
        Returns:
            The response from the next handler or a 403 Forbidden response
        """
        # Skip if disabled
        if not self._enabled:
            return await call_next(request)
        
        path = request.url.path
        
        # Check if this path should be protected
        if not self._should_check_ip(path):
            return await call_next(request)
        
        # Extract client IP
        client_ip = self._get_client_ip(request)
        if not client_ip:
            logger.warning(
                "IP whitelist check failed - could not determine client IP",
                path=path,
                method=request.method,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied - IP verification failed"},
            )
        
        # Check whitelist
        if not self._is_ip_allowed(client_ip):
            logger.warning(
                "IP whitelist check failed - IP not in whitelist",
                client_ip=client_ip,
                path=path,
                method=request.method,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied - IP address not authorized"},
            )
        
        # IP is allowed, continue to next handler
        return await call_next(request)
