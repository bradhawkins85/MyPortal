"""Smoke tests for the ``ntfy`` feature pack."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI

from app.core.config import Settings
from app.core.features import init_registry
from app.features.ntfy import PACK


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ntfy_pack_manifest_is_routeless():
    assert PACK.slug == "ntfy"
    assert PACK.version
    assert PACK.routers == ()


def test_ntfy_pack_is_enabled_by_default():
    default_feature_packs = str(Settings.model_fields["feature_packs"].default).split(",")
    assert "ntfy" in default_feature_packs

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ntfy" in env_example


def test_ntfy_pack_loads_and_reloads_cleanly():
    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("ntfy")
        loaded = registry.get("ntfy")
        assert loaded is not None
        assert loaded.pack.slug == "ntfy"
        assert not loaded.mounted_routes

        await registry.reload("ntfy")
        reloaded = registry.get("ntfy")
        assert reloaded is not None
        assert reloaded.pack.slug == "ntfy"
        assert not reloaded.mounted_routes

        await registry.unload_all()

    asyncio.run(_run())
