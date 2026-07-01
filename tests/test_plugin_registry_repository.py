from __future__ import annotations

import pytest

from app.repositories import plugin_registry


def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_ensure_registered_mysql_uses_upsert_without_insert_ignore(monkeypatch):
    executed: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr(plugin_registry.db, "is_connected", lambda: True)
    monkeypatch.setattr(plugin_registry.db, "is_sqlite", lambda: False)

    async def fake_execute(sql: str, params: tuple[str, ...]) -> None:
        executed.append((sql, params))

    monkeypatch.setattr(plugin_registry.db, "execute", fake_execute)

    await plugin_registry.ensure_registered("plugin.demo")

    assert executed == [
        (
            """
            INSERT INTO plugin_registry (slug, enabled)
            VALUES (%s, 1)
            ON DUPLICATE KEY UPDATE slug = slug
            """,
            ("plugin.demo",),
        )
    ]
    assert "INSERT IGNORE" not in executed[0][0]
    assert "ON DUPLICATE KEY UPDATE" in executed[0][0]
