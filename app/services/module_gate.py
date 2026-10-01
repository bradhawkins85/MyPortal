"""Common last-mile guard for optional integration network operations."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.repositories import integration_modules as module_repo


class ModuleDisabledError(RuntimeError):
    """Raised before I/O when an optional integration is disabled."""


async def require_module_enabled(slug: str) -> dict:
    module = await module_repo.get_module(slug)
    if not module or not module.get("enabled"):
        raise ModuleDisabledError(f"Module '{slug}' is disabled")
    return module


async def require_enabled(slug: str) -> dict:
    """Backward-compatible HTTP boundary used by route handlers."""
    try:
        return await require_module_enabled(slug)
    except ModuleDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{slug} module is disabled",
        ) from exc
