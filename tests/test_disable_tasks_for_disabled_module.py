"""Tests for auto-disabling scheduled tasks when a module is disabled."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.services import modules as modules_service
from app.services.scheduler import COMMANDS_BY_MODULE


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# COMMANDS_BY_MODULE mapping
# ---------------------------------------------------------------------------

def test_commands_by_module_is_defined():
    assert isinstance(COMMANDS_BY_MODULE, dict)
    assert len(COMMANDS_BY_MODULE) > 0


def test_known_modules_in_commands_by_module():
    expected_slugs = {"m365", "xero", "call-recordings", "unifi-talk", "tacticalrmm"}
    assert expected_slugs.issubset(set(COMMANDS_BY_MODULE.keys()))


def test_commands_by_module_values_are_sets():
    for slug, cmds in COMMANDS_BY_MODULE.items():
        assert isinstance(cmds, set), f"Commands for {slug!r} should be a set"
        assert all(isinstance(c, str) for c in cmds)


# ---------------------------------------------------------------------------
# Repository: disable_tasks_for_commands
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_disable_tasks_for_commands_executes_update(monkeypatch):
    executed_queries: list[tuple] = []

    async def fake_execute(query, params=()):
        executed_queries.append((query, params))
        return 2  # 2 rows affected

    monkeypatch.setattr(scheduled_tasks_repo.db, "execute", fake_execute)

    count = await scheduled_tasks_repo.disable_tasks_for_commands({"sync_o365", "sync_to_xero"})

    assert count == 2
    assert len(executed_queries) == 1
    query, params = executed_queries[0]
    assert "UPDATE scheduled_tasks" in query
    assert "active = 0" in query
    assert "WHERE active = 1" in query


@pytest.mark.anyio
async def test_disable_tasks_for_commands_empty_list(monkeypatch):
    executed_queries: list[tuple] = []

    async def fake_execute(query, params=()):
        executed_queries.append((query, params))  # pragma: no cover

    monkeypatch.setattr(scheduled_tasks_repo.db, "execute", fake_execute)

    count = await scheduled_tasks_repo.disable_tasks_for_commands(set())
    assert count == 0
    assert executed_queries == []


# ---------------------------------------------------------------------------
# Service: update_module disables tasks when module is disabled
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_update_module_disables_tasks_when_module_disabled(monkeypatch):
    disabled_commands: list[set] = []

    async def fake_get_module(slug):
        return {"slug": slug, "enabled": True, "settings": {}}

    async def fake_update_module_repo(slug, *, enabled=None, settings=None):
        return {"slug": slug, "enabled": enabled, "settings": settings or {}}

    async def fake_disable_tasks(commands):
        disabled_commands.append(set(commands))
        return len(commands)

    async def fake_broadcast(**kwargs):
        pass

    fake_notifier = MagicMock()
    fake_notifier.broadcast_refresh = AsyncMock()

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service.module_repo, "update_module", fake_update_module_repo)
    monkeypatch.setattr(modules_service.scheduled_tasks_repo, "disable_tasks_for_commands", fake_disable_tasks)

    result = await modules_service.update_module("xero", enabled=False, notifier=fake_notifier)

    assert result is not None
    assert len(disabled_commands) == 1
    assert disabled_commands[0] == {"sync_to_xero", "sync_to_xero_auto_send"}


@pytest.mark.anyio
async def test_update_module_does_not_disable_tasks_when_enabled(monkeypatch):
    disabled_commands: list[set] = []

    async def fake_get_module(slug):
        return {"slug": slug, "enabled": False, "settings": {}}

    async def fake_update_module_repo(slug, *, enabled=None, settings=None):
        return {"slug": slug, "enabled": enabled, "settings": settings or {}}

    async def fake_disable_tasks(commands):
        disabled_commands.append(set(commands))  # pragma: no cover

    fake_notifier = MagicMock()
    fake_notifier.broadcast_refresh = AsyncMock()

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service.module_repo, "update_module", fake_update_module_repo)
    monkeypatch.setattr(modules_service.scheduled_tasks_repo, "disable_tasks_for_commands", fake_disable_tasks)

    # Enabling a module should NOT disable any tasks
    await modules_service.update_module("xero", enabled=True, notifier=fake_notifier)

    assert disabled_commands == []


@pytest.mark.anyio
async def test_update_module_does_not_disable_tasks_when_enabled_none(monkeypatch):
    """When enabled is not changed (None), tasks should not be touched."""
    disabled_commands: list[set] = []

    async def fake_get_module(slug):
        return {"slug": slug, "enabled": True, "settings": {}}

    async def fake_update_module_repo(slug, *, enabled=None, settings=None):
        return {"slug": slug, "enabled": enabled, "settings": settings or {}}

    async def fake_disable_tasks(commands):
        disabled_commands.append(set(commands))  # pragma: no cover

    fake_notifier = MagicMock()
    fake_notifier.broadcast_refresh = AsyncMock()

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service.module_repo, "update_module", fake_update_module_repo)
    monkeypatch.setattr(modules_service.scheduled_tasks_repo, "disable_tasks_for_commands", fake_disable_tasks)

    await modules_service.update_module("xero", notifier=fake_notifier)

    assert disabled_commands == []


@pytest.mark.anyio
async def test_update_module_no_tasks_disabled_for_unknown_module(monkeypatch):
    """Disabling a module with no mapped commands should not call disable_tasks_for_commands."""
    disabled_commands: list[set] = []

    async def fake_get_module(slug):
        return {"slug": slug, "enabled": True, "settings": {}}

    async def fake_update_module_repo(slug, *, enabled=None, settings=None):
        return {"slug": slug, "enabled": enabled, "settings": settings or {}}

    async def fake_disable_tasks(commands):
        disabled_commands.append(set(commands))  # pragma: no cover

    fake_notifier = MagicMock()
    fake_notifier.broadcast_refresh = AsyncMock()

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service.module_repo, "update_module", fake_update_module_repo)
    monkeypatch.setattr(modules_service.scheduled_tasks_repo, "disable_tasks_for_commands", fake_disable_tasks)

    # "smtp" module has no mapped commands in COMMANDS_BY_MODULE
    await modules_service.update_module("smtp", enabled=False, notifier=fake_notifier)

    assert disabled_commands == []
