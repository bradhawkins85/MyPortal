"""Runtime boundaries for optional integration modules."""

from fastapi import HTTPException, status

from app.repositories import integration_modules


async def require_enabled(slug: str) -> dict:
    """Read current state and reject operations for a disabled module."""
    module = await integration_modules.get_module(slug)
    if not module or not bool(module.get("enabled")):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{slug} module is disabled",
        )
    return module
