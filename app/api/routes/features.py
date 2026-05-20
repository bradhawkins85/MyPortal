"""Admin API for hot-reloading feature packs.

Endpoints
---------
``GET  /api/features``               – list loaded packs (super-admin)
``POST /api/features/{slug}/reload`` – reload a single pack (super-admin)

The reload endpoint is CSRF-protected by the global
``CSRFMiddleware`` in ``app/main.py`` and additionally requires the
caller to be a super administrator.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.api.dependencies.auth import require_super_admin
from app.core.features import get_registry


router = APIRouter(prefix="/api/features", tags=["Feature Packs"])


@router.get("/")
async def list_features(current_user: dict = Depends(require_super_admin)) -> dict[str, list[dict]]:
    """Return metadata for every loaded feature pack."""

    registry = get_registry()
    return {"features": registry.list()}


@router.post("/{slug}/reload")
async def reload_feature(
    slug: str,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, object]:
    """Atomically reload a feature pack.

    On failure the previous version stays mounted; the error is
    surfaced in ``last_error``.
    """

    registry = get_registry()
    try:
        state = await registry.reload(slug)
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature pack '{slug}'",
        ) from exc
    except Exception as exc:  # pragma: no cover - reported via 500
        logger.bind(feature=slug).error("Reload failed: {error}", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Feature pack reload failed; see server logs.",
        ) from exc
    return {
        "slug": state.pack.slug,
        "version": state.pack.version,
        "loaded_at": state.loaded_at.isoformat(),
        "last_error": state.last_error,
        "last_reload_duration_ms": state.last_reload_duration_ms,
    }
