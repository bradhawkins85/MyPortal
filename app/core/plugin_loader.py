"""Discovery/loading utilities for out-of-tree plugins."""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from loguru import logger

from app.core.features import FeaturePack, FeatureRegistry
from app.repositories import plugin_registry as plugin_registry_repo


@dataclass
class PluginDiscovery:
    slug: str
    module_name: str
    path: Path
    pack: FeaturePack


class PluginLoader:
    """Discovers, installs, and loads plugins from configured directories."""

    def __init__(self, plugin_dirs: str | None = None) -> None:
        from app.core.config import get_settings

        settings = get_settings()
        self._plugin_dirs = self._parse_dirs(plugin_dirs or settings.plugin_dirs)
        self._slug_to_path: dict[str, Path] = {}

    @staticmethod
    def _parse_dirs(raw: str) -> list[Path]:
        parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
        if not parts:
            parts = ["./plugins"]
        resolved: list[Path] = []
        for part in parts:
            resolved.append(Path(part).expanduser().resolve())
        return resolved

    @property
    def plugin_dirs(self) -> list[Path]:
        return list(self._plugin_dirs)

    def ensure_sys_path(self) -> None:
        for directory in self._plugin_dirs:
            if not directory.exists():
                continue
            path_str = str(directory)
            if path_str not in sys.path:
                sys.path.append(path_str)

    def discover(self, dirs: Iterable[Path] | None = None) -> list[PluginDiscovery]:
        self.ensure_sys_path()
        roots = list(dirs) if dirs is not None else self._plugin_dirs
        discoveries: list[PluginDiscovery] = []
        self._slug_to_path = {}
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for candidate in sorted(root.iterdir(), key=lambda p: p.name):
                if not candidate.is_dir():
                    continue
                if not (candidate / "__init__.py").exists():
                    continue
                module_name = candidate.name
                try:
                    module = importlib.import_module(module_name)
                except Exception as exc:
                    logger.bind(plugin=module_name).error(
                        "Failed to import plugin module: {error}", error=str(exc)
                    )
                    continue
                pack: FeaturePack | None = getattr(module, "PACK", None)
                if pack is None:
                    factory = getattr(module, "get_pack", None)
                    if callable(factory):
                        pack = factory()
                if pack is None or not isinstance(pack, FeaturePack):
                    continue
                if not pack.slug.startswith("plugin."):
                    logger.bind(plugin=module_name).warning(
                        "Skipping plugin with invalid slug '{slug}'", slug=pack.slug
                    )
                    continue
                resolved = candidate.resolve()
                discoveries.append(
                    PluginDiscovery(
                        slug=pack.slug,
                        module_name=module_name,
                        path=resolved,
                        pack=pack,
                    )
                )
                self._slug_to_path[pack.slug] = resolved
        return discoveries

    def scan(self, dirs: Iterable[Path] | None = None) -> list[str]:
        return [item.slug for item in self.discover(dirs=dirs)]

    def directory_for_slug(self, slug: str) -> Path | None:
        return self._slug_to_path.get(slug)

    async def load_all(self, registry: FeatureRegistry) -> list[str]:
        discoveries = self.discover()
        loaded: list[str] = []
        for item in discoveries:
            await plugin_registry_repo.ensure_registered(item.slug)
            if not await plugin_registry_repo.is_enabled(item.slug):
                continue
            try:
                await registry.load(item.slug)
                loaded.append(item.slug)
            except Exception as exc:
                logger.bind(plugin=item.slug).error(
                    "Failed to load plugin: {error}", error=str(exc)
                )
        return loaded

    async def list_admin_rows(self, registry: FeatureRegistry) -> list[dict]:
        discoveries = {item.slug: item for item in self.discover()}
        db_rows = {row["slug"]: row for row in await plugin_registry_repo.list_entries()}
        slugs = sorted(set(discoveries.keys()) | set(db_rows.keys()))
        rows: list[dict] = []
        for slug in slugs:
            discovery = discoveries.get(slug)
            state = registry.get(slug)
            db_row = db_rows.get(slug, {})
            pack = state.pack if state is not None else (discovery.pack if discovery else None)
            rows.append(
                {
                    "slug": slug,
                    "available": discovery is not None,
                    "enabled": bool(db_row.get("enabled", True)),
                    "installed_at": db_row.get("installed_at"),
                    "version": pack.version if pack else "",
                    "author": pack.author if pack else "",
                    "description": pack.description if pack else "",
                    "homepage": pack.homepage if pack else "",
                    "loaded_at": state.loaded_at.isoformat() if state else None,
                    "in_flight": state.in_flight if state else 0,
                    "last_error": state.last_error if state else None,
                    "last_reload_duration_ms": state.last_reload_duration_ms if state else None,
                }
            )
        return rows

    def _target_root(self) -> Path:
        root = self._plugin_dirs[0]
        root.mkdir(parents=True, exist_ok=True)
        return root

    async def install_from_directory(self, source_path: str) -> list[str]:
        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise ValueError("Plugin source directory does not exist.")
        if not (source / "__init__.py").exists():
            raise ValueError("Plugin source must contain __init__.py.")
        target_root = self._target_root()
        target = target_root / source.name
        if target.exists():
            raise ValueError(f"Plugin directory already exists: {target.name}")
        shutil.copytree(source, target)
        return self.scan(dirs=[target_root])

    @staticmethod
    def _extract_zip_safely(zip_path: Path, destination: Path) -> None:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                name = member.filename
                if not name:
                    continue
                posix_path = PurePosixPath(name)
                if posix_path.is_absolute() or ".." in posix_path.parts:
                    raise ValueError("Zip contains unsafe path traversal entries.")
                out_path = destination / Path(*posix_path.parts)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if member.is_dir():
                    out_path.mkdir(parents=True, exist_ok=True)
                    continue
                with archive.open(member, "r") as src, out_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    async def install_from_zip(self, zip_bytes: bytes) -> list[str]:
        target_root = self._target_root()
        with tempfile.TemporaryDirectory(prefix="plugin-install-") as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / "plugin.zip"
            zip_path.write_bytes(zip_bytes)
            extract_root = temp_path / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            self._extract_zip_safely(zip_path, extract_root)
            copied_any = False
            for candidate in sorted(extract_root.rglob("*")):
                if not candidate.is_dir():
                    continue
                if not (candidate / "__init__.py").exists():
                    continue
                relative = candidate.relative_to(extract_root)
                if len(relative.parts) != 1:
                    continue
                destination = target_root / candidate.name
                if destination.exists():
                    raise ValueError(f"Plugin directory already exists: {candidate.name}")
                shutil.copytree(candidate, destination)
                copied_any = True
            if not copied_any:
                raise ValueError("Zip archive did not contain any plugin package directories.")
        return self.scan(dirs=[target_root])


_plugin_loader: PluginLoader | None = None


def init_plugin_loader(plugin_dirs: str | None = None) -> PluginLoader:
    global _plugin_loader
    _plugin_loader = PluginLoader(plugin_dirs=plugin_dirs)
    return _plugin_loader


def get_plugin_loader() -> PluginLoader:
    global _plugin_loader
    if _plugin_loader is None:
        _plugin_loader = PluginLoader()
    return _plugin_loader
