"""Admin API for plugin lifecycle management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from loguru import logger

from app.api.dependencies.auth import require_super_admin
from app.core.features import get_registry
from app.core.plugin_loader import get_plugin_loader
from app.repositories import plugin_registry as plugin_registry_repo


router = APIRouter(prefix="/api/plugins", tags=["Plugins"])


@router.get("/")
async def list_plugins(current_user: dict = Depends(require_super_admin)) -> dict[str, list[dict]]:
    loader = get_plugin_loader()
    rows = await loader.list_admin_rows(get_registry())
    return {"plugins": rows}


@router.post("/{slug}/enable")
async def enable_plugin(slug: str, current_user: dict = Depends(require_super_admin)) -> dict[str, str]:
    if not slug.startswith("plugin."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid plugin slug.")
    await plugin_registry_repo.set_enabled(slug, True)
    registry = get_registry()
    try:
        await registry.load(slug)
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown plugin '{slug}'"
        ) from exc
    return {"slug": slug, "status": "enabled"}


@router.post("/{slug}/disable")
async def disable_plugin(slug: str, current_user: dict = Depends(require_super_admin)) -> dict[str, str]:
    if not slug.startswith("plugin."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid plugin slug.")
    await plugin_registry_repo.set_enabled(slug, False)
    await get_registry().unload(slug)
    return {"slug": slug, "status": "disabled"}


@router.post("/install")
async def install_plugin(
    plugin_path: str | None = Form(default=None),
    plugin_zip: UploadFile | None = File(default=None),
    current_user: dict = Depends(require_super_admin),
) -> dict[str, object]:
    if bool(plugin_path) == bool(plugin_zip):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of plugin_path or plugin_zip.",
        )

    loader = get_plugin_loader()
    try:
        if plugin_path:
            installed = await loader.install_from_directory(plugin_path)
        else:
            payload = await plugin_zip.read()  # type: ignore[union-attr]
            installed = await loader.install_from_zip(payload)
        loaded = await loader.load_all(get_registry())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Plugin install failed: {error}", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Plugin installation failed.",
        ) from exc

    return {"installed": sorted(set(installed)), "loaded": sorted(set(loaded))}
