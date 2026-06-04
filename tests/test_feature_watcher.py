"""Tests for ``app.core.feature_watcher.FeaturePackWatcher``."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.core.feature_watcher import FeaturePackWatcher


class _FakeRegistry:
    def __init__(self) -> None:
        self.reload_calls: list[str] = []

    def list(self) -> list[dict[str, str]]:  # noqa: D401 - matches real signature
        return [{"slug": "tickets"}]

    async def reload(self, slug: str) -> None:
        self.reload_calls.append(slug)


def test_is_meaningful_ignores_pyc_and_pycache():
    changes = [
        (1, "/app/features/tickets/__pycache__/foo.cpython-312.pyc"),
        (1, "/app/features/tickets/.swp.foo"),
    ]
    assert FeaturePackWatcher._is_meaningful(changes, Path("/app/features/tickets")) is False


def test_is_meaningful_accepts_python_source():
    changes = [
        (1, "/app/features/tickets/routes.py"),
    ]
    assert FeaturePackWatcher._is_meaningful(changes, Path("/app/features/tickets")) is True


def test_directory_for_resolves_existing_pack():
    directory = FeaturePackWatcher._directory_for("tickets")
    assert directory is not None
    assert directory.name == "tickets"
    assert (directory / "__init__.py").exists()


def test_directory_for_missing_pack_returns_none():
    assert FeaturePackWatcher._directory_for("definitely-not-a-real-pack") is None


def test_directory_for_resolves_existing_plugin():
    plugins_root = Path(__file__).resolve().parent.parent / "plugins"
    sys.path.append(str(plugins_root))
    try:
        directory = FeaturePackWatcher._directory_for("plugin.hello_world")
        assert directory is not None
        assert directory.name == "hello_world"
        assert (directory / "__init__.py").exists()
    finally:
        if str(plugins_root) in sys.path:
            sys.path.remove(str(plugins_root))


@pytest.mark.asyncio
async def test_reload_with_logging_invokes_registry():
    registry = _FakeRegistry()
    watcher = FeaturePackWatcher(registry)  # type: ignore[arg-type]

    await watcher._reload_with_logging("tickets")

    assert registry.reload_calls == ["tickets"]


@pytest.mark.asyncio
async def test_reload_with_logging_swallows_errors():
    class _BoomRegistry(_FakeRegistry):
        async def reload(self, slug: str) -> None:
            raise RuntimeError("boom")

    registry = _BoomRegistry()
    watcher = FeaturePackWatcher(registry)  # type: ignore[arg-type]

    # Must not propagate so a bad reload doesn't kill the watcher loop.
    await watcher._reload_with_logging("tickets")


@pytest.mark.asyncio
async def test_stop_cancels_running_tasks():
    registry = _FakeRegistry()
    watcher = FeaturePackWatcher(registry)  # type: ignore[arg-type]

    async def _never_returns() -> None:
        await asyncio.sleep(60)

    watcher._tasks["tickets"] = asyncio.create_task(_never_returns())

    await watcher.stop()

    assert watcher._tasks == {}
