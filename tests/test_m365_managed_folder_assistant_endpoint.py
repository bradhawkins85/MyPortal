"""Tests for Managed Folder Assistant mailbox endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service

_JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(db, "connect", noop)
    monkeypatch.setattr(db, "disconnect", noop)
    monkeypatch.setattr(db, "run_migrations", noop)
    monkeypatch.setattr(scheduler_service, "start", noop)
    monkeypatch.setattr(scheduler_service, "stop", noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def _super_admin_context():
    async def fake_load_license_context(request, **kwargs):
        user = {"id": 1, "is_super_admin": True, "company_id": 42}
        return user, None, None, 42, None

    return fake_load_license_context


def _non_admin_context():
    async def fake_load_license_context(request, **kwargs):
        user = {"id": 2, "is_super_admin": False, "company_id": 42}
        return user, None, None, 42, None

    return fake_load_license_context


def test_start_managed_folder_assistant_for_mailbox(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(
        main_module.m365_service,
        "get_user_mailboxes",
        AsyncMock(return_value=[{"user_principal_name": "alice@example.com"}]),
    )
    monkeypatch.setattr(
        main_module.m365_service,
        "get_shared_mailboxes",
        AsyncMock(return_value=[{"user_principal_name": "shared@example.com"}]),
    )
    start_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main_module.m365_service, "start_managed_folder_assistant", start_mock)

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/start-managed-folder-assistant",
            json={"upn": "alice@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {"started": True}
    start_mock.assert_awaited_once_with(42, "alice@example.com")


def test_start_managed_folder_assistant_unknown_mailbox_returns_404(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(main_module.m365_service, "get_user_mailboxes", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.m365_service, "get_shared_mailboxes", AsyncMock(return_value=[]))

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/start-managed-folder-assistant",
            json={"upn": "missing@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 404


def test_start_managed_folder_assistant_requires_upn(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/start-managed-folder-assistant",
            json={},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 400


def test_start_managed_folder_assistant_rejects_non_super_admin(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _non_admin_context())

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/start-managed-folder-assistant",
            json={"upn": "alice@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 403


def test_start_managed_folder_assistant_all_mailboxes(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    start_all_mock = AsyncMock(return_value={"started": 5, "failed": 1})
    monkeypatch.setattr(
        main_module.m365_service,
        "start_managed_folder_assistant_all_mailboxes",
        start_all_mock,
    )

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/start-managed-folder-assistant/all",
            json={},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {"started": 5, "failed": 1}
    start_all_mock.assert_awaited_once_with(42)


def test_start_managed_folder_assistant_all_rejects_non_super_admin(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _non_admin_context())

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/start-managed-folder-assistant/all",
            json={},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 403
