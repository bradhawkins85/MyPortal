"""Feature pack registry and loader.

A *feature pack* is a self-contained area of MyPortal (e.g. ``tickets``,
``knowledge_base``) that can be loaded, unloaded, and reloaded at
runtime without restarting the whole application.

This module implements the contract and a minimal in-process registry.
It does **not** migrate any of the existing inline route handlers from
``app.main`` — that work is performed pack-by-pack in follow-up PRs.

Contract
========

Core packs live at ``app.features.<slug>``. External plugins use slugs
prefixed with ``plugin.`` and are imported from directories configured
via ``PLUGIN_DIRS``. Each module exposes a module-level ``PACK``
attribute (or a ``get_pack()`` callable returning one) that is an
instance of :class:`FeaturePack`.

The loader guarantees:

* Loading is idempotent — loading an already-loaded pack returns the
  cached :class:`FeaturePackState`.
* Reloading is atomic per-pack: callers see either the old router or
  the new router, never a mix.  In-flight requests against the old
  router are tracked and allowed to drain before unload completes.
* A failed reload is a no-op for traffic: the previous version stays
  mounted and the error is captured on the pack state for surfacing in
  the admin UI / logs.
* Each pack reload runs under a per-pack :class:`asyncio.Lock` so two
  concurrent reload requests for the same pack are serialised; reloads
  of *different* packs run independently.

Hot-reload limits
=================

Hot-reload cannot safely cover changes to:

* Middleware, the FastAPI app object, or anything in ``app/main.py``
  outside a pack.
* Python dependencies in ``pyproject.toml`` (those require a process
  restart — see ``docs/zero_downtime_upgrades.md``).
* Destructive database migrations.  Packs must follow the additive /
  expand-contract migration discipline described in
  ``docs/feature_packs.md``.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from fastapi import APIRouter, FastAPI
from loguru import logger


HookCallable = Callable[[], Awaitable[None] | None]


def module_name_for_slug(slug: str) -> str:
    """Resolve a feature/plugin slug to a Python module import path."""

    if slug.startswith("plugin."):
        return slug.split(".", 1)[1]
    return f"app.features.{slug}"


def _parse_semver(value: str) -> tuple[int, int, int]:
    raw = (value or "").strip()
    if not raw:
        return (0, 0, 0)
    if raw.startswith(("v", "V")):
        raw = raw[1:]
    # Accept metadata/suffixes (e.g. "1.2.3-rc1"), compare numeric core only.
    numeric = re.split(r"[-+]", raw, maxsplit=1)[0]
    parts = [p.strip() for p in numeric.split(".") if p.strip()]
    nums: list[int] = []
    for part in parts[:3]:
        nums.append(int(part) if part.isdigit() else 0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _app_version() -> str:
    global _app_version_fallback_warned
    try:
        return importlib.metadata.version("myportal")
    except Exception:
        if not _app_version_fallback_warned:
            logger.warning(
                "Unable to resolve installed package version for 'myportal'; "
                "falling back to 0.1.0 for plugin version checks."
            )
            _app_version_fallback_warned = True
        return "0.1.0"


def _validate_min_app_version(pack: "FeaturePack") -> None:
    required = (pack.min_app_version or "").strip()
    if not required:
        return
    current = _app_version()
    if _parse_semver(current) < _parse_semver(required):
        raise RuntimeError(
            f"Feature/plugin '{pack.slug}' requires app version >= {required}; current is {current}."
        )


def _make_tracking_endpoint(original: Callable[..., Any], state: "FeaturePackState") -> Callable[..., Any]:
    """Return a wrapper around ``original`` that increments ``state.in_flight``."""

    if asyncio.iscoroutinefunction(original):
        async def _async_wrapped(*args: Any, **kwargs: Any) -> Any:
            state.in_flight += 1
            try:
                return await original(*args, **kwargs)
            finally:
                state.in_flight = max(0, state.in_flight - 1)

        _async_wrapped.__name__ = getattr(original, "__name__", "tracked_endpoint")
        _async_wrapped.__doc__ = getattr(original, "__doc__", None)
        _async_wrapped.__wrapped__ = original  # type: ignore[attr-defined]
        return _async_wrapped

    def _sync_wrapped(*args: Any, **kwargs: Any) -> Any:
        state.in_flight += 1
        try:
            return original(*args, **kwargs)
        finally:
            state.in_flight = max(0, state.in_flight - 1)

    _sync_wrapped.__name__ = getattr(original, "__name__", "tracked_endpoint")
    _sync_wrapped.__doc__ = getattr(original, "__doc__", None)
    _sync_wrapped.__wrapped__ = original  # type: ignore[attr-defined]
    return _sync_wrapped


@dataclass
class FeaturePack:
    """Manifest describing a single feature pack.

    Attributes
    ----------
    slug:
        Stable, URL-safe identifier (``"tickets"``).  Used in the reload
        API path and log context.
    version:
        Human-readable version string for diagnostics.  Bump on every
        meaningful change to the pack.
    routers:
        FastAPI ``APIRouter`` instances to mount.  Each is wrapped in a
        per-pack parent router so unload can remove them atomically.
    startup:
        Optional async callable invoked after routers are mounted.
    shutdown:
        Optional async callable invoked before routers are removed.
    background_jobs:
        Optional iterable of awaitables started via
        ``asyncio.create_task`` during load.  They are cancelled on
        unload.  Use :mod:`app.services.singleton_jobs` to gate jobs
        that must run on only one instance in a multi-instance
        deployment.
    """

    slug: str
    version: str = "0.0.0"
    routers: tuple[APIRouter, ...] = ()
    startup: HookCallable | None = None
    shutdown: HookCallable | None = None
    background_jobs: tuple[Callable[[], Awaitable[None]], ...] = ()
    author: str = ""
    description: str = ""
    homepage: str = ""
    min_app_version: str = ""


@dataclass
class FeaturePackState:
    """Runtime state held by the registry for a loaded pack."""

    pack: FeaturePack
    parent_router: APIRouter
    loaded_at: datetime
    mounted_routes: list[Any] = field(default_factory=list)
    background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    in_flight: int = 0
    last_error: str | None = None
    last_reload_duration_ms: float | None = None


class FeatureRegistry:
    """Tracks loaded feature packs and brokers load/unload/reload calls."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._states: dict[str, FeaturePackState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
        self._drain_timeout_seconds: float = 10.0

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "slug": state.pack.slug,
                "version": state.pack.version,
                "loaded_at": state.loaded_at.isoformat(),
                "in_flight": state.in_flight,
                "last_error": state.last_error,
                "last_reload_duration_ms": state.last_reload_duration_ms,
            }
            for state in self._states.values()
        ]

    def get(self, slug: str) -> FeaturePackState | None:
        return self._states.get(slug)

    def all_loaded(self) -> bool:
        """Whether every registered pack is in a healthy ``last_error is None`` state."""

        return all(state.last_error is None for state in self._states.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _lock_for(self, slug: str) -> asyncio.Lock:
        lock = self._locks.get(slug)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[slug] = lock
        return lock

    @staticmethod
    def _import_pack(slug: str) -> FeaturePack:
        module_name = module_name_for_slug(slug)
        module = importlib.import_module(module_name)
        pack: FeaturePack | None = getattr(module, "PACK", None)
        if pack is None:
            factory = getattr(module, "get_pack", None)
            if callable(factory):
                pack = factory()
        if pack is None:
            raise RuntimeError(
                f"Feature module '{module_name}' does not expose a 'PACK' "
                f"attribute or 'get_pack()' callable."
            )
        if pack.slug != slug:
            raise RuntimeError(
                f"Feature module '{module_name}' declares slug "
                f"'{pack.slug}' but was imported as '{slug}'."
            )
        _validate_min_app_version(pack)
        return pack

    @staticmethod
    def _purge_sys_modules(slug: str) -> None:
        prefix = module_name_for_slug(slug)
        for name in [n for n in list(sys.modules) if n == prefix or n.startswith(prefix + ".")]:
            sys.modules.pop(name, None)

    def _build_parent_router(self, pack: FeaturePack) -> APIRouter:
        """Build a parent router that hosts all the pack's routes."""

        parent = APIRouter()
        for child in pack.routers:
            parent.include_router(child)
        return parent

    @staticmethod
    def _wrap_routes_with_state(routes: Iterable[Any], state: "FeaturePackState") -> None:
        """Wrap each ``APIRoute`` in ``routes`` to bump ``state.in_flight``.

        We patch both ``route.endpoint`` *and* ``route.dependant.call``
        because FastAPI invokes the dependant when servicing a request,
        and rebuild ``route.app`` so the patched call is actually used.
        """

        from fastapi.routing import APIRoute

        for route in routes:
            if not isinstance(route, APIRoute):
                continue
            original = route.endpoint
            wrapped = _make_tracking_endpoint(original, state)
            route.endpoint = wrapped
            if route.dependant is not None:
                route.dependant.call = wrapped
            try:
                # Use FastAPI's request_response (not Starlette's) so the
                # ``fastapi_inner_astack`` scope key the dependency
                # resolver expects is populated.
                from fastapi.routing import request_response as _request_response

                route.app = _request_response(route.get_route_handler())
            except Exception:  # pragma: no cover - defensive
                pass

    async def _start_background_jobs(self, state: FeaturePackState) -> None:
        for factory in state.pack.background_jobs:
            try:
                coro = factory()
            except Exception as exc:  # pragma: no cover - factory misuse
                logger.bind(feature=state.pack.slug).error(
                    "Background job factory raised: {error}", error=str(exc)
                )
                continue
            if not asyncio.iscoroutine(coro):
                logger.bind(feature=state.pack.slug).error(
                    "Background job factory did not return a coroutine"
                )
                continue
            task_name = f"feature:{state.pack.slug}:{getattr(factory, '__name__', 'job')}"
            state.background_tasks.append(asyncio.create_task(coro, name=task_name))

    async def _stop_background_jobs(self, state: FeaturePackState) -> None:
        for task in state.background_tasks:
            task.cancel()
        if state.background_tasks:
            await asyncio.gather(*state.background_tasks, return_exceptions=True)
        state.background_tasks.clear()

    async def _drain(self, state: FeaturePackState) -> None:
        deadline = time.monotonic() + self._drain_timeout_seconds
        while state.in_flight > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if state.in_flight > 0:
            logger.bind(feature=state.pack.slug).warning(
                "Unload draining timed out with {n} request(s) still in flight",
                n=state.in_flight,
            )

    def _remove_parent_router(self, state: "FeaturePackState") -> None:
        """Detach the pack's routes from the FastAPI application."""

        target_ids = {id(route) for route in state.mounted_routes}
        if not target_ids:
            return
        self._app.router.routes = [
            route for route in self._app.router.routes if id(route) not in target_ids
        ]

    async def _run_hook(self, hook: HookCallable | None, slug: str, name: str) -> None:
        if hook is None:
            return
        try:
            result = hook()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.bind(feature=slug).error("{name} hook failed: {error}", name=name, error=str(exc))
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def load(self, slug: str) -> FeaturePackState:
        """Import + mount the pack.  Idempotent."""

        async with self._lock_for(slug):
            if slug in self._states:
                return self._states[slug]
            pack = self._import_pack(slug)
            state = await self._mount(pack)
            self._states[slug] = state
            logger.bind(feature=slug).info(
                "Feature pack loaded version={version}", version=pack.version
            )
            return state

    async def _mount(self, pack: FeaturePack) -> FeaturePackState:
        parent = self._build_parent_router(pack)
        state = FeaturePackState(
            pack=pack,
            parent_router=parent,
            loaded_at=datetime.now(timezone.utc),
        )
        # Snapshot the app's route list, include the parent, then
        # capture the newly-appended routes so we can both wrap them
        # for in-flight tracking and remove them on unload.  FastAPI's
        # ``include_router`` deep-copies routes from ``parent`` into
        # ``self._app.router.routes``, so we cannot rely on object
        # identity with ``parent.routes``.
        before = len(self._app.router.routes)
        self._app.include_router(parent)
        new_routes = self._app.router.routes[before:]
        state.mounted_routes = list(new_routes)
        self._wrap_routes_with_state(new_routes, state)
        await self._run_hook(pack.startup, pack.slug, "startup")
        await self._start_background_jobs(state)
        return state

    async def unload(self, slug: str) -> None:
        async with self._lock_for(slug):
            state = self._states.pop(slug, None)
            if state is None:
                return
            try:
                await self._stop_background_jobs(state)
                await self._run_hook(state.pack.shutdown, slug, "shutdown")
            finally:
                self._remove_parent_router(state)
                await self._drain(state)
                self._purge_sys_modules(slug)
            logger.bind(feature=slug).info("Feature pack unloaded")

    async def reload(self, slug: str) -> FeaturePackState:
        """Atomically reload a pack.

        On import error the previous instance is left mounted and the
        error is recorded on the (new) state.
        """

        async with self._lock_for(slug):
            previous = self._states.get(slug)
            started = time.monotonic()
            try:
                # Drop cached modules so importlib re-reads from disk.
                self._purge_sys_modules(slug)
                new_pack = self._import_pack(slug)
            except Exception as exc:
                logger.bind(feature=slug).error(
                    "Reload import failed; keeping previous version: {error}",
                    error=str(exc),
                )
                if previous is not None:
                    previous.last_error = str(exc)
                    return previous
                raise

            # Mount the new version *before* tearing down the old so we
            # never have a window where the pack is unmounted.
            try:
                new_state = await self._mount(new_pack)
            except Exception as exc:
                logger.bind(feature=slug).error(
                    "Reload mount failed; keeping previous version: {error}",
                    error=str(exc),
                )
                if previous is not None:
                    previous.last_error = str(exc)
                    return previous
                raise

            self._states[slug] = new_state

            if previous is not None:
                try:
                    await self._stop_background_jobs(previous)
                    await self._run_hook(previous.pack.shutdown, slug, "shutdown")
                finally:
                    self._remove_parent_router(previous)
                    await self._drain(previous)

            new_state.last_reload_duration_ms = (time.monotonic() - started) * 1000.0
            logger.bind(feature=slug).info(
                "Feature pack reloaded version={version} duration_ms={ms:.1f}",
                version=new_pack.version,
                ms=new_state.last_reload_duration_ms,
            )
            return new_state

    async def load_many(self, slugs: Iterable[str]) -> None:
        for slug in slugs:
            try:
                await self.load(slug)
            except Exception as exc:
                logger.bind(feature=slug).error(
                    "Failed to load feature pack: {error}", error=str(exc)
                )

    async def unload_all(self) -> None:
        for slug in list(self._states.keys()):
            try:
                await self.unload(slug)
            except Exception as exc:  # pragma: no cover - defensive
                logger.bind(feature=slug).error(
                    "Failed to unload feature pack: {error}", error=str(exc)
                )


_registry: FeatureRegistry | None = None
_app_version_fallback_warned = False


def init_registry(app: FastAPI) -> FeatureRegistry:
    """Initialise the global registry for ``app`` and return it."""

    global _registry
    _registry = FeatureRegistry(app)
    return _registry


def get_registry() -> FeatureRegistry:
    if _registry is None:
        raise RuntimeError("Feature registry has not been initialised")
    return _registry
