"""Smoke tests for the ``m365_admin`` feature pack."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI

from app.core.config import Settings
from app.core.features import init_registry
from app.features.m365_admin import PACK


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_m365_admin_pack_manifest_is_routeless():
    assert PACK.slug == "m365_admin"
    assert PACK.version
    assert PACK.routers == ()


def test_m365_admin_pack_is_enabled_by_default():
    default_feature_packs = str(Settings.model_fields["feature_packs"].default).split(",")
    assert "m365_admin" in default_feature_packs

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "m365_admin" in env_example


def test_m365_admin_pack_loads_and_reloads_cleanly():
    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("m365_admin")
        loaded = registry.get("m365_admin")
        assert loaded is not None
        assert loaded.pack.slug == "m365_admin"
        assert not loaded.mounted_routes

        await registry.reload("m365_admin")
        reloaded = registry.get("m365_admin")
        assert reloaded is not None
        assert reloaded.pack.slug == "m365_admin"
        assert not reloaded.mounted_routes

        await registry.unload_all()

    asyncio.run(_run())
