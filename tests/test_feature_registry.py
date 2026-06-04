"""Tests for the feature-pack registry/loader.

These exercise the contract documented in ``app/core/features.py``:

* load is idempotent
* reload swaps routers atomically; failed imports leave the previous
  pack mounted
* unload cancels background jobs and removes routes from the app
* in-flight request tracking blocks unload until requests drain (best
  effort; we assert the counter behaviour directly to avoid
  test flakiness)
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.features import FeaturePack, FeatureRegistry, _parse_semver, init_registry


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


@pytest.fixture
def registry(app: FastAPI) -> FeatureRegistry:
    return init_registry(app)


def _write_pack(tmp_path: Path, slug: str, version: str, response: str) -> None:
    """Write a tiny feature pack to ``app/features/<slug>/__init__.py``.

    The pack is written into the live ``app/features`` directory because
    that's where ``importlib.import_module('app.features.<slug>')``
    looks.  Each test cleans up after itself by deleting the slug's
    directory in a finaliser.
    """

    pkg_dir = Path(__file__).resolve().parent.parent / "app" / "features" / slug
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from fastapi import APIRouter
            from app.core.features import FeaturePack

            router = APIRouter(prefix="/_t_{slug}", tags=["test"])

            @router.get("/ping")
            async def ping() -> dict[str, str]:
                return {{"v": "{response}"}}

            PACK = FeaturePack(
                slug="{slug}",
                version="{version}",
                routers=(router,),
            )
            """
        ).lstrip()
    )


@pytest.fixture
def temp_pack_slug(tmp_path):
    slug = "_pytest_pack_demo"
    yield slug
    pkg_dir = Path(__file__).resolve().parent.parent / "app" / "features" / slug
    if pkg_dir.exists():
        for child in pkg_dir.glob("*"):
            child.unlink()
        pkg_dir.rmdir()
    # Drop cached modules so the next test doesn't see this one's code.
    for name in [n for n in list(sys.modules) if n.startswith(f"app.features.{slug}")]:
        sys.modules.pop(name, None)


def test_load_is_idempotent(tmp_path, app, registry, temp_pack_slug):
    _write_pack(tmp_path, temp_pack_slug, "1.0.0", "first")

    async def run() -> None:
        s1 = await registry.load(temp_pack_slug)
        s2 = await registry.load(temp_pack_slug)
        assert s1 is s2
        assert s1.pack.version == "1.0.0"

    asyncio.run(run())

    with TestClient(app) as client:
        resp = client.get(f"/_t_{temp_pack_slug}/ping")
        assert resp.status_code == 200
        assert resp.json() == {"v": "first"}


def test_reload_swaps_router_in_place(tmp_path, app, registry, temp_pack_slug):
    _write_pack(tmp_path, temp_pack_slug, "1.0.0", "v1")

    async def run() -> None:
        await registry.load(temp_pack_slug)
        # Rewrite the pack on disk and reload.
        _write_pack(tmp_path, temp_pack_slug, "2.0.0", "v2")
        state = await registry.reload(temp_pack_slug)
        assert state.pack.version == "2.0.0"
        assert state.last_error is None

    asyncio.run(run())

    with TestClient(app) as client:
        resp = client.get(f"/_t_{temp_pack_slug}/ping")
        assert resp.json() == {"v": "v2"}
        # And only one matching route exists – the old one was removed.
        matches = [r for r in app.routes if getattr(r, "path", "") == f"/_t_{temp_pack_slug}/ping"]
        assert len(matches) == 1


def test_reload_failure_keeps_previous_version(tmp_path, app, registry, temp_pack_slug):
    _write_pack(tmp_path, temp_pack_slug, "1.0.0", "good")

    async def run() -> None:
        await registry.load(temp_pack_slug)

        # Now corrupt the pack: a syntax error means import fails.
        pkg_init = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "features"
            / temp_pack_slug
            / "__init__.py"
        )
        pkg_init.write_text("this is not valid python )(")

        state = await registry.reload(temp_pack_slug)
        # Previous instance kept; last_error populated.
        assert state.pack.version == "1.0.0"
        assert state.last_error is not None

    asyncio.run(run())

    with TestClient(app) as client:
        resp = client.get(f"/_t_{temp_pack_slug}/ping")
        assert resp.status_code == 200
        assert resp.json() == {"v": "good"}


def test_unload_removes_routes(tmp_path, app, registry, temp_pack_slug):
    _write_pack(tmp_path, temp_pack_slug, "1.0.0", "x")

    async def run() -> None:
        await registry.load(temp_pack_slug)
        await registry.unload(temp_pack_slug)
        assert registry.get(temp_pack_slug) is None

    asyncio.run(run())

    with TestClient(app) as client:
        resp = client.get(f"/_t_{temp_pack_slug}/ping")
        assert resp.status_code == 404


def test_in_flight_counter_increments(tmp_path, app, registry, temp_pack_slug):
    """The counter must rise while an endpoint is executing."""

    pkg_dir = Path(__file__).resolve().parent.parent / "app" / "features" / temp_pack_slug
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            import asyncio
            from fastapi import APIRouter
            from app.core.features import FeaturePack

            router = APIRouter(prefix="/_t_{temp_pack_slug}", tags=["test"])

            @router.get("/slow")
            async def slow() -> dict[str, str]:
                await asyncio.sleep(0.1)
                return {{"ok": "1"}}

            PACK = FeaturePack(slug="{temp_pack_slug}", version="1.0.0", routers=(router,))
            """
        ).lstrip()
    )

    async def run() -> None:
        state = await registry.load(temp_pack_slug)
        assert state.in_flight == 0
        # Find the wrapped endpoint and call it directly.
        from fastapi.routing import APIRoute

        wrapped = None
        for route in state.mounted_routes:
            if isinstance(route, APIRoute) and route.path.endswith("/slow"):
                wrapped = route.endpoint
        assert wrapped is not None
        task = asyncio.create_task(wrapped())
        await asyncio.sleep(0.02)
        assert state.in_flight == 1
        await task
        assert state.in_flight == 0

    asyncio.run(run())


def test_load_external_plugin_slug(tmp_path, app, registry):
    plugin_root = tmp_path / "plugins"
    package_dir = plugin_root / "demo_external"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter
            from app.core.features import FeaturePack

            router = APIRouter(prefix="/_plugin_demo", tags=["plugin-test"])

            @router.get("/ping")
            async def ping() -> dict[str, str]:
                return {"ok": "plugin"}

            PACK = FeaturePack(
                slug="plugin.demo_external",
                version="1.0.0",
                routers=(router,),
            )
            """
        ).lstrip()
    )

    sys.path.append(str(plugin_root))
    try:
        asyncio.run(registry.load("plugin.demo_external"))
        with TestClient(app) as client:
            resp = client.get("/_plugin_demo/ping")
            assert resp.status_code == 200
            assert resp.json() == {"ok": "plugin"}
    finally:
        if str(plugin_root) in sys.path:
            sys.path.remove(str(plugin_root))
        for name in [n for n in list(sys.modules) if n.startswith("demo_external")]:
            sys.modules.pop(name, None)


def test_parse_semver_handles_short_and_suffix_versions():
    assert _parse_semver("") == (0, 0, 0)
    assert _parse_semver("1") == (1, 0, 0)
    assert _parse_semver("1.2") == (1, 2, 0)
    assert _parse_semver("1.2.3-rc1") == (1, 2, 3)
    assert _parse_semver("v2.5.9+build.7") == (2, 5, 9)
