from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import integration_operations


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_build_operations_center_aggregates_health_and_conflicts(monkeypatch):
    command_map = {
        "sync_to_xero": frozenset({"xero"}),
        "sync_m365_data": frozenset({"m365-admin"}),
    }
    modules = [
        {
            "slug": "xero",
            "name": "Xero",
            "enabled": True,
            "settings": {
                "client_id": "client",
                "client_secret": "secret",
                "tenant_id": "tenant",
                "token_expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            },
        },
        {
            "slug": "m365-admin",
            "name": "Microsoft 365 Admin",
            "enabled": True,
            "settings": {
                "client_id": "client",
                "client_secret": "secret",
                "tenant_id": "",
                "client_secret_expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            },
        },
        {
            "slug": "smtp2go",
            "name": "SMTP2Go",
            "enabled": False,
            "settings": {},
        },
    ]
    tasks = [
        {
            "id": 1,
            "name": "Sync invoices",
            "command": "sync_to_xero",
            "company_id": None,
            "active": True,
            "cron": "0 2 * * *",
        },
        {
            "id": 2,
            "name": "Auto-send invoices",
            "command": "sync_to_xero",
            "company_id": None,
            "active": True,
            "cron": "1 2 * * *",
        },
        {
            "id": 3,
            "name": "Sync M365 data",
            "command": "sync_m365_data",
            "company_id": 7,
            "active": True,
            "cron": "30 3 * * *",
        },
    ]
    runs = [
        {"task_id": 1, "status": "succeeded"},
        {"task_id": 2, "status": "failed"},
        {"task_id": 3, "status": "succeeded"},
    ]
    webhook_events = [
        {
            "name": "Xero webhook delivery",
            "target_url": "https://example.com/api/integration-modules/xero/webhook",
            "source_url": None,
            "status": "failed",
            "metadata": {"module_slug": "xero"},
        }
    ]

    async def fake_list_tasks(include_inactive: bool = False):
        assert include_inactive is True
        return tasks

    async def fake_list_recent_runs(limit: int = 200):
        assert limit == 200
        return runs

    async def fake_list_events(limit: int = 500):
        assert limit == 500
        return webhook_events

    monkeypatch.setattr(
        integration_operations,
        "get_settings",
        lambda: SimpleNamespace(
            default_timezone="UTC",
            m365_client_secret_renewal_days=30,
            integration_credential_warning_days=30,
        ),
    )
    monkeypatch.setattr(
        integration_operations,
        "modules_for_command",
        lambda command: command_map.get(command, frozenset()),
    )
    monkeypatch.setattr(
        integration_operations,
        "COMMANDS_BY_MODULE",
        {"xero": {"sync_to_xero"}, "m365-admin": {"sync_m365_data"}},
    )
    next_runs = {
        1: datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc),
        2: datetime(2026, 9, 17, 2, 1, tzinfo=timezone.utc),
        3: datetime(2026, 9, 17, 3, 30, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(
        integration_operations.cron_calendar,
        "calculate_next_run",
        lambda task, **_: next_runs.get(task.get("id")),
    )
    monkeypatch.setattr(
        integration_operations.scheduled_tasks_repo, "list_tasks", fake_list_tasks
    )
    monkeypatch.setattr(
        integration_operations.scheduled_tasks_repo, "list_recent_runs", fake_list_recent_runs
    )
    monkeypatch.setattr(
        integration_operations.webhook_events_repo, "list_events", fake_list_events
    )

    result = await integration_operations.build_operations_center(modules)

    assert result["summary"]["enabled_modules"] == 2
    assert result["summary"]["failed_webhooks"] == 1
    assert result["summary"]["task_conflicts"] == 2
    assert any(
        item["summary"].startswith("Duplicate schedule")
        for item in result["dependency_graph"]["conflicts"]
    )

    xero = next(item for item in result["module_health"] if item["slug"] == "xero")
    assert xero["status_key"] == "warning"
    assert xero["failed_webhook_count"] == 1
    assert xero["slo_achieved_percent"] == pytest.approx(33.3)
    assert xero["error_budget_overrun"] == 1

    m365_admin = next(
        item for item in result["module_health"] if item["slug"] == "m365-admin"
    )
    assert m365_admin["status_key"] == "setup"
    assert "Tenant Id" in m365_admin["missing_fields"]

    assert any(
        alert["module_slug"] == "m365-admin" and alert["severity"] == "danger"
        for alert in result["credential_alerts"]
    )
    assert any(
        step["module_slug"] == "m365-admin"
        and "Add Tenant Id" in step["issues"][0]
        for step in result["setup_steps"]
    )


@pytest.mark.anyio
async def test_build_operations_center_flags_missing_scheduled_task_for_enabled_module(monkeypatch):
    modules = [
        {
            "slug": "huntress",
            "name": "Huntress",
            "enabled": True,
            "settings": {"api_key": "key"},
        }
    ]

    async def fake_list_tasks(include_inactive: bool = False):
        return []

    async def fake_list_recent_runs(limit: int = 200):
        return []

    async def fake_list_events(limit: int = 500):
        return []

    monkeypatch.setattr(
        integration_operations,
        "get_settings",
        lambda: SimpleNamespace(
            default_timezone="UTC",
            m365_client_secret_renewal_days=30,
            integration_credential_warning_days=30,
        ),
    )
    monkeypatch.setattr(
        integration_operations,
        "modules_for_command",
        lambda command: frozenset({"huntress"}) if command == "sync_huntress" else frozenset(),
    )
    monkeypatch.setattr(
        integration_operations, "COMMANDS_BY_MODULE", {"huntress": {"sync_huntress"}}
    )
    monkeypatch.setattr(
        integration_operations.scheduled_tasks_repo, "list_tasks", fake_list_tasks
    )
    monkeypatch.setattr(
        integration_operations.scheduled_tasks_repo, "list_recent_runs", fake_list_recent_runs
    )
    monkeypatch.setattr(
        integration_operations.webhook_events_repo, "list_events", fake_list_events
    )

    result = await integration_operations.build_operations_center(modules)

    assert result["summary"]["setup_steps"] == 1
    assert result["setup_steps"][0]["issues"] == ["Create at least one scheduled task"]
