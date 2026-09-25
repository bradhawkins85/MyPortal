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


def test_first_connection_does_not_inventory_admin_credentials_placeholder(
    monkeypatch,
):
    placeholder = {
        "company_id": 1,
        "tenant_id": "",
        "client_id": "",
        "client_secret": "",
        "admin_client_id": "admin-client",
    }
    candidate = {"id": 12, "company_id": 1, "state": "pending"}
    ensure_legacy = AsyncMock()
    stage_candidate = AsyncMock(return_value=candidate)
    upsert_credentials = AsyncMock(return_value={"company_id": 1})
    monkeypatch.setattr(
        m365.m365_repo, "get_credentials", AsyncMock(return_value=placeholder)
    )
    monkeypatch.setattr(m365.connection_repo, "ensure_legacy", ensure_legacy)
    monkeypatch.setattr(m365.connection_repo, "stage_candidate", stage_candidate)
    monkeypatch.setattr(m365.m365_repo, "upsert_credentials", upsert_credentials)
    monkeypatch.setattr(m365, "_encrypt", lambda value: f"encrypted:{value}")

    result = asyncio.run(
        m365.stage_connection_candidate(
            1,
            "tenant",
            {
                "client_id": "client",
                "client_secret": "secret",
                "app_object_id": "app",
                "service_principal_object_id": "service-principal",
                "client_secret_key_id": "key",
                "client_secret_expires_at": None,
            },
        )
    )

    assert result == candidate
    ensure_legacy.assert_not_awaited()
    stage_candidate.assert_awaited_once_with(
        company_id=1,
        tenant_id="tenant",
        client_id="client",
        client_secret="encrypted:secret",
        app_object_id="app",
        service_principal_object_id="service-principal",
        client_secret_key_id="key",
        client_secret_expires_at=None,
    )
    upsert_credentials.assert_awaited_once_with(
        company_id=1,
        tenant_id="tenant",
        client_id="client",
        client_secret="encrypted:secret",
        refresh_token=None,
        access_token=None,
        token_expires_at=None,
        app_object_id="app",
        client_secret_key_id="key",
        client_secret_expires_at=None,
    )


def test_legacy_inventory_uses_next_version_and_restores_matching_row(monkeypatch):
    active = {"id": 7, "company_id": 1, "state": "active", "version": 2}
    monkeypatch.setattr(
        m365_connections, "get_active", AsyncMock(side_effect=[None, active])
    )
    execute = AsyncMock()
    inventory = AsyncMock()
    monkeypatch.setattr(m365_connections.db, "execute", execute)
    monkeypatch.setattr(m365_connections, "inventory_dependencies", inventory)

    result = asyncio.run(
        m365_connections.ensure_legacy(
            1,
            {
                "tenant_id": "tenant",
                "client_id": "client",
                "client_secret": "encrypted-secret",
            },
        )
    )

    assert result == active
    sql, params = execute.await_args.args
    assert "COALESCE(MAX(version), 0) + 1" in sql
    assert "state = 'active'" in sql
    assert params[-1] == 1
    inventory.assert_awaited_once_with(7, 1, "tenant")


def test_existing_credentials_are_reencrypted_for_legacy_inventory(monkeypatch):
    current = {
        "tenant_id": "tenant",
        "client_id": "client",
        "client_secret": "decrypted-secret",
    }
    ensure_legacy = AsyncMock()
    monkeypatch.setattr(m365.m365_repo, "get_credentials", AsyncMock(return_value=current))
    monkeypatch.setattr(m365.connection_repo, "ensure_legacy", ensure_legacy)
    monkeypatch.setattr(
        m365.connection_repo, "stage_candidate", AsyncMock(return_value={"id": 12})
    )
    upsert_credentials = AsyncMock()
    monkeypatch.setattr(m365.m365_repo, "upsert_credentials", upsert_credentials)
    monkeypatch.setattr(m365, "_encrypt", lambda value: f"encrypted:{value}")

    asyncio.run(
        m365.stage_connection_candidate(
            1, "tenant", {"client_id": "new", "client_secret": "new-secret"}
        )
    )

    inventoried = ensure_legacy.await_args.args[1]
    assert inventoried["client_secret"] == "encrypted:decrypted-secret"
    assert current["client_secret"] == "decrypted-secret"
    upsert_credentials.assert_not_awaited()


def test_repeated_dependency_inventory_uses_warning_free_upserts(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(m365_connections.db, "execute", execute)

    asyncio.run(m365_connections.inventory_dependencies(20, 1, "tenant"))

    assert execute.await_count == 5
    for call in execute.await_args_list:
        sql = call.args[0]
        assert "INSERT IGNORE" not in sql
        assert "ON DUPLICATE KEY UPDATE" in sql


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
