from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, Dict, Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


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
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "retry_after": retry_after},
                headers={
                    "Retry-After": f"{int(retry_after or self.rate_limiter.window_seconds)}",
                },
            )

        response = await call_next(request)
        return response
