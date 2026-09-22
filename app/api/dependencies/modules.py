"""Runtime guards for integration-module HTTP surfaces.

Disabled and unknown integrations deliberately share the same stable ``503``
contract.  Routes remain mounted so toggling a module takes effect immediately;
callers receive ``{"detail": "Integration module '<slug>' is unavailable"}``
until the catalogue entry exists and is enabled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status

from app.services import modules as modules_service
from app.services.component_availability import get_component_availability


def require_module_enabled(
    slug: str,
) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Build a FastAPI dependency requiring an enabled integration module.

    The unredacted module is returned so downstream dependencies can reuse its
    runtime settings.  A 503 (rather than a 404) is intentional: these are
    stable integration endpoints and their callers need to distinguish a
    temporarily disabled integration from a nonexistent HTTP route.
    """

    async def dependency() -> dict[str, Any]:
        if not get_component_availability().module_available(slug):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        module = await modules_service.get_module(slug, redact=False)
        if not module or not module.get("enabled"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Integration module '{slug}' is unavailable",
            )
        return module

    return dependency


__all__ = ["require_module_enabled"]
