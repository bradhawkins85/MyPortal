from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.repositories import m365_connections
from app.services import m365


def test_connection_migration_is_additive_and_rolling_safe():
    sql = Path("migrations/395_m365_connection_migrations.sql").read_text()
    assert "-- phase: expand" in sql
    assert "m365_connections" in sql
    assert "m365_connection_dependencies" in sql
    assert "DROP " not in sql.upper()
    assert "company_m365_credentials" not in sql


def test_provisioning_never_searches_or_deletes_by_display_name(monkeypatch):
    get_roles = AsyncMock(return_value=("00000000-0000-0000-0000-000000000001", {}))
    build_access = AsyncMock(return_value=([], [], []))
    post = AsyncMock(side_effect=[
        {"id": "00000000-0000-0000-0000-000000000002", "appId": "client"},
        {"id": "00000000-0000-0000-0000-000000000003"},
        {"secretText": "secret", "keyId": "key"},
    ])
    monkeypatch.setattr(m365, "_get_sp_app_role_ids", get_roles)
    monkeypatch.setattr(m365, "_build_required_resource_access", build_access)
    monkeypatch.setattr(m365, "_graph_post", post)
    monkeypatch.setattr(m365.asyncio, "create_task", lambda coro, **kwargs: coro.close())

    result = asyncio.run(m365.provision_app_registration(
        access_token="token", display_name="Shared display name"
    ))

    assert result["client_id"] == "client"
    assert all(call.args[1] != "https://graph.microsoft.com/v1.0/applications?$filter=displayName eq 'Shared display name'" for call in post.await_args_list)
    assert not hasattr(m365, "_delete_existing_apps_by_display_name")


def test_in_place_repair_uses_stable_object_identity(monkeypatch):
    monkeypatch.setattr(m365, "_get_sp_app_role_ids", AsyncMock(return_value=("00000000-0000-0000-0000-000000000001", {})))
    monkeypatch.setattr(m365, "_build_required_resource_access", AsyncMock(return_value=([], [], [])))
    patch = AsyncMock(return_value={})
    post = AsyncMock(return_value={"secretText": "secret", "keyId": "key"})
    monkeypatch.setattr(m365, "_graph_patch", patch)
    monkeypatch.setattr(m365, "_graph_post", post)
    monkeypatch.setattr(m365.asyncio, "create_task", lambda coro, **kwargs: coro.close())

    asyncio.run(m365.provision_app_registration(
        access_token="token", app_object_id="00000000-0000-0000-0000-000000000002",
        client_id="client", service_principal_object_id="00000000-0000-0000-0000-000000000003",
    ))

    assert "/applications/00000000-0000-0000-0000-000000000002" in patch.await_args.args[1]
    assert not any(call.args[1] == "https://graph.microsoft.com/v1.0/applications" for call in post.await_args_list)


def test_failed_cutover_restores_previous_active_connection(monkeypatch):
    old = {"id": 10, "company_id": 1, "state": "active"}
    candidate = {
        "id": 11, "company_id": 1, "state": "pending", "tenant_id": "tenant",
        "client_id": "new", "client_secret": "encrypted", "app_object_id": "app",
        "verification_tenant": 1, "verification_workload": 1, "verification_renewal": 1,
    }
    monkeypatch.setattr(m365_connections, "get", AsyncMock(return_value=candidate))
    monkeypatch.setattr(m365_connections, "get_active", AsyncMock(return_value=old))

    calls = []
    async def execute(sql, params):
        calls.append((sql, params))
        if "UPDATE company_m365_credentials" in sql:
            raise RuntimeError("database failure")
    monkeypatch.setattr(m365_connections.db, "execute", execute)

    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield True
    monkeypatch.setattr(m365_connections.db, "acquire_lock", lock)

    with pytest.raises(RuntimeError, match="database failure"):
        asyncio.run(m365_connections.activate(1, 11))

    assert any("state = 'active' WHERE id" in sql and params == (10,) for sql, params in calls)
    assert any("state = 'pending' WHERE id" in sql and params == (11,) for sql, params in calls)


def test_retirement_requires_empty_dependency_inventory(monkeypatch):
    monkeypatch.setattr(m365_connections, "get", AsyncMock(return_value={"id": 4, "state": "rollback"}))
    monkeypatch.setattr(m365_connections, "dependency_inventory", AsyncMock(return_value=[
        {"dependency_type": "delegated_mailbox", "dependency_key": "7"}
    ]))
    execute = AsyncMock()
    monkeypatch.setattr(m365_connections.db, "execute", execute)

    with pytest.raises(ValueError, match="dependent resources"):
        asyncio.run(m365_connections.retire(4))
    execute.assert_not_awaited()
