from __future__ import annotations

import asyncio
import io
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.features import init_registry
from app.core.plugin_loader import PluginLoader


def _write_plugin(root: Path, name: str, slug: str) -> None:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from fastapi import APIRouter
            from app.core.features import FeaturePack

            router = APIRouter(prefix="/_plugin_test_{name}")

            @router.get("/ping")
            async def ping():
                return {{"slug": "{slug}"}}

            PACK = FeaturePack(slug="{slug}", version="1.0.0", routers=(router,))
            """
        ).lstrip()
    )


@pytest.mark.asyncio
async def test_plugin_loader_discovers_and_loads_plugins(tmp_path, monkeypatch):
    _write_plugin(tmp_path, "demo_plugin", "plugin.demo_plugin")

    async def _noop_ensure(slug: str) -> None:
        return None

    async def _always_enabled(slug: str) -> bool:
        return True

    monkeypatch.setattr("app.repositories.plugin_registry.ensure_registered", _noop_ensure)
    monkeypatch.setattr("app.repositories.plugin_registry.is_enabled", _always_enabled)

    app = FastAPI()
    registry = init_registry(app)
    loader = PluginLoader(plugin_dirs=str(tmp_path))
    loaded = await loader.load_all(registry)

    assert "plugin.demo_plugin" in loaded
    with TestClient(app) as client:
        resp = client.get("/_plugin_test_demo_plugin/ping")
        assert resp.status_code == 200
        assert resp.json()["slug"] == "plugin.demo_plugin"


def test_plugin_loader_rejects_path_traversal_in_zip(tmp_path):
    loader = PluginLoader(plugin_dirs=str(tmp_path / "plugins"))
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.py", "print('oops')")

    with pytest.raises(ValueError, match="unsafe path"):
        asyncio.run(loader.install_from_zip(payload.getvalue()))


def test_plugin_loader_rejects_absolute_paths_in_zip(tmp_path):
    loader = PluginLoader(plugin_dirs=str(tmp_path / "plugins"))
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("/etc/passwd", "x")

    with pytest.raises(ValueError, match="unsafe path"):
        asyncio.run(loader.install_from_zip(payload.getvalue()))
