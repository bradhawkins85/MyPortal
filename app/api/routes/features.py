"""Admin API for hot-reloading feature packs/plugins.

Endpoints
---------
``GET  /api/features``               – list loaded packs/plugins (super-admin)
``POST /api/features/{slug}/reload`` – reload a single pack/plugin (super-admin)

The reload endpoint is CSRF-protected by the global
``CSRFMiddleware`` in ``app/main.py`` and additionally requires the
caller to be a super administrator.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.api.dependencies.auth import require_super_admin
from app.core.features import get_registry
from app.services.component_availability import get_component_availability


router = APIRouter(prefix="/api/features", tags=["Feature Packs"])


@router.get("/")
async def list_features(current_user: dict = Depends(require_super_admin)) -> dict[str, list[dict]]:
    """Return metadata for every loaded feature pack/plugin."""

    registry = get_registry()
    availability = get_component_availability()
    return {"features": [feature for feature in registry.list()
                         if availability.feature_pack_available(str(feature.get("slug") or ""))]}


@router.post("/{slug}/reload")
async def reload_feature(
    slug: str,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, object]:
    """Atomically reload a feature pack or plugin.

    On failure the previous version stays mounted; the error is
    surfaced in ``last_error``.
    """

    registry = get_registry()
    if not get_component_availability().feature_pack_available(slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Feature pack '{slug}' is disabled by deployment configuration",
        )
    try:
        state = await registry.reload(slug)
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature pack/plugin '{slug}'",
        ) from exc
    except Exception as exc:  # pragma: no cover - reported via 500
        logger.bind(feature=slug).error("Reload failed: {error}", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Feature/plugin reload failed; see server logs.",
        ) from exc
    return {
        "slug": state.pack.slug,
        "version": state.pack.version,
        "loaded_at": state.loaded_at.isoformat(),
        "last_error": state.last_error,
        "last_reload_duration_ms": state.last_reload_duration_ms,
    }
