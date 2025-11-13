from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable, Deque, Dict, Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.logging import log_warning


class SimpleRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, float | None]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = self.window_seconds - (now - bucket[0])
                return False, max(retry_after, 0.0)
            bucket.append(now)
            return True, None


class EndpointRateLimiter:
    """Rate limiter that supports per-endpoint limits with custom key generation."""
    
    def __init__(self) -> None:
        self._limiters: Dict[str, SimpleRateLimiter] = {}
        self._path_configs: list[tuple[str, str, SimpleRateLimiter, Callable[[Request], str]]] = []
    
    def add_limit(
        self,
        path: str,
        method: str,
        limit: int,
        window_seconds: int,
        key_func: Callable[[Request], str] | None = None,
    ) -> None:
        """Add a rate limit for a specific endpoint.
        
        Args:
            path: URL path to rate limit (e.g., "/auth/login")
            method: HTTP method (e.g., "POST")
            limit: Maximum number of requests
            window_seconds: Time window in seconds
            key_func: Function to generate rate limit key from request (defaults to IP-based)
        """
        config_key = f"{method}:{path}"
        limiter = SimpleRateLimiter(limit=limit, window_seconds=window_seconds)
        self._limiters[config_key] = limiter
        
        if key_func is None:
            key_func = self._default_key_func
        
        self._path_configs.append((path, method.upper(), limiter, key_func))
    
    @staticmethod
    def _default_key_func(request: Request) -> str:
        """Extract client IP from request."""
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        else:
            client = request.client
            client_ip = client.host if client else "anonymous"
        return client_ip or "anonymous"
    
    async def check(self, request: Request) -> tuple[bool, float | None, str | None]:
        """Check if request is allowed.
        
        Returns:
            Tuple of (allowed, retry_after, reason)
        """
        path = request.url.path
        method = request.method.upper()
        
        for config_path, config_method, limiter, key_func in self._path_configs:
            if path == config_path and method == config_method:
                key = key_func(request)
                allowed, retry_after = await limiter.check(key)
                reason = f"{method} {path}" if not allowed else None
                return allowed, retry_after, reason
        
        # No specific limit configured for this endpoint
        return True, None, None


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        rate_limiter: SimpleRateLimiter,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.rate_limiter = rate_limiter
        self.exempt_paths = tuple(exempt_paths or ())

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return await call_next(request)

        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        else:
            client = request.client
            client_ip = client.host if client else "anonymous"

        allowed, retry_after = await self.rate_limiter.check(client_ip or "anonymous")
        if not allowed:
            log_warning(
                "Rate limit exceeded",
                client_ip=client_ip,
                path=path,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "retry_after": retry_after},
                headers={
                    "Retry-After": f"{int(retry_after or self.rate_limiter.window_seconds)}",
                },
            )

        response = await call_next(request)
        return response


class EndpointRateLimiterMiddleware(BaseHTTPMiddleware):
    """Middleware for endpoint-specific rate limiting."""
    
    def __init__(
        self,
        app,
        *,
        endpoint_limiter: EndpointRateLimiter,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.endpoint_limiter = endpoint_limiter
        self.exempt_paths = tuple(exempt_paths or ())
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.exempt_paths):
            return await call_next(request)
        
        allowed, retry_after, reason = await self.endpoint_limiter.check(request)
        if not allowed:
            client_ip = request.headers.get("x-forwarded-for")
            if client_ip:
                client_ip = client_ip.split(",")[0].strip()
            else:
                client = request.client
                client_ip = client.host if client else "anonymous"
            
            log_warning(
                "Endpoint rate limit exceeded",
                client_ip=client_ip,
                endpoint=reason,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded for {reason}",
                    "retry_after": retry_after,
                },
                headers={
                    "Retry-After": f"{int(retry_after or 60)}",
                },
            )
        
        response = await call_next(request)
        return response
