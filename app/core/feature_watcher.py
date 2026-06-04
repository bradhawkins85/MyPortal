"""Dev-only file watcher that reloads a single feature pack on change.

Disabled by default; enable by setting ``FEATURE_PACK_WATCH=true`` in
the environment (typically only in dev). The watcher uses
``watchfiles.awatch`` to subscribe to ``app/features/<slug>/`` for
every loaded pack and debounces bursts of file events so that an IDE
save touching multiple files only triggers one reload per pack.

In a production deployment leave this off and reload packs via
``POST /api/features/{slug}/reload`` from the admin UI instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable

from app.core.logging import log_error, log_info
from app.core.features import module_name_for_slug

try:  # pragma: no cover - import-time guard for environments without watchfiles
    from watchfiles import Change, awatch
except ImportError:  # pragma: no cover
    Change = None  # type: ignore[assignment]
    awatch = None  # type: ignore[assignment]


# Re-import + atomic-router-swap typically completes in <500 ms; pick a
# debounce that batches a typical IDE "save all" burst into one reload.
_DEBOUNCE_SECONDS = 0.5


class FeaturePackWatcher:
    """Watch ``app/features/<slug>/`` and reload a pack on file changes."""

    def __init__(self, registry: "FeatureRegistry") -> None:  # noqa: F821 - forward ref
        self._registry = registry
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    def is_available(self) -> bool:
        """Return ``True`` only when ``watchfiles`` is importable."""

        return awatch is not None

    async def start(self) -> None:
        """Start one watch task per loaded pack.

        Safe to call multiple times — existing watchers are left alone
        and new packs picked up.
        """

        if not self.is_available():
            log_error(
                "FEATURE_PACK_WATCH is enabled but the 'watchfiles' "
                "package is not installed; auto-reload disabled."
            )
            return

        self._stopping = False
        for state in self._registry.list():
            slug = state["slug"]
            if slug in self._tasks and not self._tasks[slug].done():
                continue
            directory = self._directory_for(slug)
            if directory is None or not directory.exists():
                continue
            log_info("Starting feature pack watcher", feature=slug, path=str(directory))
            self._tasks[slug] = asyncio.create_task(
                self._watch_pack(slug, directory), name=f"feature-watch-{slug}"
            )

    async def stop(self) -> None:
        """Cancel every watch task and wait for them to exit."""

        self._stopping = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # pragma: no cover - best effort cleanup
                pass
        self._tasks.clear()

    @staticmethod
    def _directory_for(slug: str) -> Path | None:
        try:
            from importlib import import_module

            module = import_module(module_name_for_slug(slug))
        except Exception:  # pragma: no cover - defensive
            return None
        spec_file = getattr(module, "__file__", None)
        if not spec_file:
            return None
        return Path(spec_file).resolve().parent

    async def _watch_pack(self, slug: str, directory: Path) -> None:
        try:
            async for changes in awatch(  # type: ignore[misc]
                directory, recursive=True, stop_event=None
            ):
                if self._stopping:
                    return
                if not self._is_meaningful(changes, directory):
                    continue
                # Debounce: pull any follow-up events that arrive within
                # the window into the same reload.
                await asyncio.sleep(_DEBOUNCE_SECONDS)
                await self._reload_with_logging(slug)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - watcher must never crash the loop
            log_error(
                "Feature pack watcher crashed",
                feature=slug,
                error=str(exc),
            )

    @staticmethod
    def _is_meaningful(changes: Iterable[tuple], directory: Path) -> bool:
        """Filter out cache/temp files so editors don't trigger reloads needlessly."""

        for _change_type, raw_path in changes:
            path = Path(raw_path)
            name = path.name
            if name.endswith(".pyc") or name.endswith("~") or name.startswith("."):
                continue
            if "__pycache__" in path.parts:
                continue
            return True
        return False

    async def _reload_with_logging(self, slug: str) -> None:
        log_info("Auto-reloading feature pack", feature=slug)
        try:
            await self._registry.reload(slug)
        except Exception as exc:
            log_error(
                "Feature pack auto-reload failed",
                feature=slug,
                error=str(exc),
            )


__all__ = ["FeaturePackWatcher"]
