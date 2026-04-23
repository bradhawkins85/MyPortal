"""Tests for the rebuilt server-rendered dashboard.

The previous customisable card grid (``app.services.dashboard_cards``) and
its ``/api/dashboard/...`` endpoints have been removed in favour of an
opinionated, fixed-section dashboard built by :mod:`app.services.dashboard`.

These tests cover:

* Permission gating (super admin sees system-health and unassigned tickets;
  regular users do not).
* Graceful degradation when repositories raise.
* The migration that purges old per-user layout preferences is idempotent.
* The home route renders the new sections.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.services import dashboard as dashboard_service


# ---------------------------------------------------------------------------
# Shared startup scaffolding
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def _request(active_company_id: int | None = None, available_companies=None, membership=None):
    state = SimpleNamespace(
        available_companies=available_companies if available_companies is not None else [],
        active_company_id=active_company_id,
        active_membership=membership,
    )
    return SimpleNamespace(state=state)


def _user(**overrides: Any) -> dict[str, Any]:
    base = {"id": 1, "email": "user@example.com", "is_super_admin": False}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# build_dashboard – section gating
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_dashboard_super_admin_includes_all_sections(monkeypatch):
    async def fake_count_for_user(user_id, **kwargs):
        return 3

    async def fake_count_tickets(**kwargs):
        # Unassigned only
        return 5

    async def fake_webhook_count(state):
        return {"pending": 2, "in_progress": 1, "failed": 4}.get(state, 0)

    async def fake_unread(*args, **kwargs):
        return 7

    async def fake_notifications(*args, **kwargs):
        return []

    async def fake_changes(*args, **kwargs):
        return []

    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets_for_user", fake_count_for_user)
    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets", fake_count_tickets)
    monkeypatch.setattr(dashboard_service.webhook_events_repo, "count_events_by_status", fake_webhook_count)
    monkeypatch.setattr(dashboard_service.notifications_repo, "count_notifications", fake_unread)
    monkeypatch.setattr(dashboard_service.notifications_repo, "list_notifications", fake_notifications)
    monkeypatch.setattr(dashboard_service.change_log_repo, "list_change_log_entries", fake_changes)

    payload = await dashboard_service.build_dashboard(
        _request(),
        _user(is_super_admin=True),
    )

    assert "greeting" in payload
    assert "attention" in payload
    assert "quick_actions" in payload
    assert "recent_activity" in payload
    assert "system_health" in payload
    assert payload["unread_notifications"] == 7

    keys = {item["key"] for item in payload["attention"]["items"]}
    assert "tickets.my_open" in keys
    assert "tickets.unassigned" in keys
    assert "webhooks.failed" in keys

    assert payload["system_health"]["webhook_failed"] == 4


@pytest.mark.asyncio
async def test_build_dashboard_regular_user_excludes_admin_sections(monkeypatch):
    async def fake_count_for_user(user_id, **kwargs):
        return 1

    async def fake_count_tickets(**kwargs):
        # Should NOT be called for non-admin
        raise AssertionError("count_tickets must not be called for non-admin")

    async def fake_webhook_count(state):
        # Should NOT be called for non-admin since neither attention nor health uses it
        raise AssertionError("webhook count must not be called for non-admin")

    async def fake_unread(*args, **kwargs):
        return 0

    async def fake_notifications(*args, **kwargs):
        return []

    async def fake_changes(*args, **kwargs):
        # Should NOT be called for non-admin
        raise AssertionError("list_change_log_entries must not be called for non-admin")

    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets_for_user", fake_count_for_user)
    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets", fake_count_tickets)
    monkeypatch.setattr(dashboard_service.webhook_events_repo, "count_events_by_status", fake_webhook_count)
    monkeypatch.setattr(dashboard_service.notifications_repo, "count_notifications", fake_unread)
    monkeypatch.setattr(dashboard_service.notifications_repo, "list_notifications", fake_notifications)
    monkeypatch.setattr(dashboard_service.change_log_repo, "list_change_log_entries", fake_changes)

    payload = await dashboard_service.build_dashboard(_request(), _user())

    assert "system_health" not in payload
    keys = {item["key"] for item in payload["attention"]["items"]}
    assert "tickets.my_open" in keys
    assert "tickets.unassigned" not in keys
    assert "webhooks.failed" not in keys
    assert payload["recent_activity"]["changes"] == []


@pytest.mark.asyncio
async def test_build_dashboard_all_clear_when_no_attention(monkeypatch):
    async def zero(*args, **kwargs):
        return 0

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets_for_user", zero)
    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets", zero)
    monkeypatch.setattr(dashboard_service.webhook_events_repo, "count_events_by_status", zero)
    monkeypatch.setattr(dashboard_service.notifications_repo, "count_notifications", zero)
    monkeypatch.setattr(dashboard_service.notifications_repo, "list_notifications", empty)
    monkeypatch.setattr(dashboard_service.change_log_repo, "list_change_log_entries", empty)

    payload = await dashboard_service.build_dashboard(
        _request(), _user(is_super_admin=True)
    )
    assert payload["attention"]["items"] == []
    assert payload["attention"]["all_clear"] is True


@pytest.mark.asyncio
async def test_build_dashboard_handles_repository_failures(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("backend down")

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets_for_user", boom)
    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets", boom)
    monkeypatch.setattr(dashboard_service.webhook_events_repo, "count_events_by_status", boom)
    monkeypatch.setattr(dashboard_service.notifications_repo, "count_notifications", boom)
    monkeypatch.setattr(dashboard_service.notifications_repo, "list_notifications", empty)
    monkeypatch.setattr(dashboard_service.change_log_repo, "list_change_log_entries", empty)

    payload = await dashboard_service.build_dashboard(
        _request(), _user(is_super_admin=True)
    )
    # All sections still rendered, attention is "all clear" because counts fall to 0.
    assert payload["attention"]["all_clear"] is True
    assert payload["unread_notifications"] == 0
    assert payload["system_health"]["webhook_failed"] == 0


@pytest.mark.asyncio
async def test_quick_actions_does_not_include_switch_company(monkeypatch):
    async def zero(*args, **kwargs):
        return 0

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets_for_user", zero)
    monkeypatch.setattr(dashboard_service.tickets_repo, "count_tickets", zero)
    monkeypatch.setattr(dashboard_service.webhook_events_repo, "count_events_by_status", zero)
    monkeypatch.setattr(dashboard_service.notifications_repo, "count_notifications", zero)
    monkeypatch.setattr(dashboard_service.notifications_repo, "list_notifications", empty)
    monkeypatch.setattr(dashboard_service.change_log_repo, "list_change_log_entries", empty)

    payload = await dashboard_service.build_dashboard(
        _request(
            available_companies=[
                {"company_id": 1, "company_name": "A"},
                {"company_id": 2, "company_name": "B"},
            ]
        ),
        _user(),
    )
    labels = {a["label"] for a in payload["quick_actions"]["actions"]}
    assert "Switch company" not in labels
    assert payload["greeting"]["can_switch_company"] is True


# ---------------------------------------------------------------------------
# Migration: purge old layout preferences
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "219_remove_dashboard_layout_preferences.sql"


def test_purge_migration_exists_and_targets_old_layout_key():
    assert MIGRATION.exists(), "migration file must exist"
    sql = MIGRATION.read_text()
    assert "user_preferences" in sql
    assert "dashboard:layout:v1" in sql
    # DELETE WHERE is naturally idempotent — running it twice deletes nothing
    # the second time. Make sure the migration is just a DELETE (no destructive
    # DDL) so re-runs are safe.
    upper = sql.upper()
    assert "DROP " not in upper
    assert "TRUNCATE" not in upper
    assert "DELETE FROM USER_PREFERENCES" in upper


@pytest.mark.asyncio
async def test_purge_migration_is_idempotent_on_sqlite(tmp_path, monkeypatch):
    """Run the DELETE twice against an in-memory SQLite table to prove it
    doesn't fail on a second run."""
    import aiosqlite

    sql = MIGRATION.read_text()

    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute(
            "CREATE TABLE user_preferences ("
            " user_id INTEGER NOT NULL,"
            " preference_key TEXT NOT NULL,"
            " preference_value TEXT NOT NULL,"
            " PRIMARY KEY (user_id, preference_key))"
        )
        await conn.execute(
            "INSERT INTO user_preferences(user_id, preference_key, preference_value) "
            "VALUES (1, 'dashboard:layout:v1', '[]'), (1, 'other:key', '[]')"
        )
        await conn.commit()

        # First run removes the row
        await conn.executescript(sql)
        await conn.commit()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM user_preferences WHERE preference_key = 'dashboard:layout:v1'"
        )
        assert (await cursor.fetchone())[0] == 0

        # Second run is a no-op
        await conn.executescript(sql)
        await conn.commit()
        cursor = await conn.execute("SELECT COUNT(*) FROM user_preferences")
        # Unrelated row preserved
        assert (await cursor.fetchone())[0] == 1


# ---------------------------------------------------------------------------
# /  (home page) — integration smoke
# ---------------------------------------------------------------------------

@pytest.fixture
def authenticated_user(monkeypatch):
    async def fake_require_authenticated_user(request):
        return {"id": 1, "email": "user@example.com", "is_super_admin": False}, None

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)


@pytest.fixture
def stub_overview(monkeypatch):
    async def fake_overview(request, user):
        return {
            "greeting": {
                "greeting": "Good morning",
                "name": "user",
                "role": "Member",
                "company_name": None,
                "can_switch_company": False,
                "switch_url": "/companies",
            },
            "attention": {"items": [], "all_clear": True},
            "quick_actions": {"actions": [{"label": "New ticket", "href": "/tickets/new", "variant": "primary"}]},
            "recent_activity": {
                "notifications": [],
                "changes": [],
                "notifications_link": "/notifications",
                "changes_link": None,
            },
            "unread_notifications": 0,
        }

    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)


@pytest.fixture
def stub_base_context(monkeypatch):
    async def fake_build_base_context(request, user, extra=None):
        context = {
            "request": request,
            "user": user,
            "current_user": user,
            "is_super_admin": user.get("is_super_admin", False),
            "available_companies": [],
            "active_company_id": None,
            "active_company": None,
            "title": "Dashboard",
            "notification_unread_count": 0,
            "plausible_config": {"enabled": False},
            "csrf_token": None,
            "enable_auto_refresh": False,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)


def test_home_renders_new_dashboard_sections(authenticated_user, stub_overview, stub_base_context):
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    body = response.text

    # Old custom-grid markup must not appear.
    assert "data-dashboard-grid" not in body
    assert "/static/js/dashboard.js" not in body
    assert "/api/dashboard/" not in body

    # New sections present.
    assert "Needs your attention" in body
    assert "Quick actions" in body
    assert "Recent activity" in body
    assert "Good morning" in body


def test_home_shows_attention_items(authenticated_user, stub_base_context, monkeypatch):
    async def fake_overview(request, user):
        return {
            "greeting": {"greeting": "Hello", "name": "user", "role": "Super admin",
                         "company_name": "Acme", "can_switch_company": True, "switch_url": "/companies"},
            "attention": {
                "items": [
                    {"key": "tickets.unassigned", "label": "Unassigned tickets",
                     "count": 3, "severity": "warning", "href": "/admin/tickets?assigned=unassigned"},
                ],
                "all_clear": False,
            },
            "quick_actions": {"actions": []},
            "recent_activity": {"notifications": [], "changes": [],
                                "notifications_link": "/notifications", "changes_link": None},
            "unread_notifications": 0,
            "system_health": {
                "webhook_pending": 0, "webhook_in_progress": 0, "webhook_failed": 2,
                "webhooks_url": "/admin/webhooks", "service_status_url": "/service-status",
            },
        }

    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)

    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Unassigned tickets" in body
    assert "/admin/tickets?assigned=unassigned" in body
    assert "System health" in body
