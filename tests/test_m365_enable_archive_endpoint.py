"""Tests for the /m365/mailboxes/enable-archive endpoint."""

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


def test_enable_archive_invokes_service_and_returns_success(monkeypatch):
    mailboxes = [
        {"user_principal_name": "alice@example.com", "has_archive": False},
        {"user_principal_name": "bob@example.com", "has_archive": True},
    ]
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(
        main_module.m365_service,
        "get_user_mailboxes",
        AsyncMock(return_value=mailboxes),
    )
    enable_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main_module.m365_service, "enable_user_archive", enable_mock)

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/enable-archive",
            json={"upn": "alice@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {"enabled": True}
    enable_mock.assert_awaited_once_with(42, "alice@example.com")


def test_enable_archive_skips_when_already_enabled(monkeypatch):
    mailboxes = [{"user_principal_name": "bob@example.com", "has_archive": True}]
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(
        main_module.m365_service,
        "get_user_mailboxes",
        AsyncMock(return_value=mailboxes),
    )
    enable_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main_module.m365_service, "enable_user_archive", enable_mock)

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/enable-archive",
            json={"upn": "bob@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 200
    assert response.json().get("already_enabled") is True
    enable_mock.assert_not_called()


def test_enable_archive_unknown_upn_returns_404(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())
    monkeypatch.setattr(
        main_module.m365_service,
        "get_user_mailboxes",
        AsyncMock(return_value=[]),
    )

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/enable-archive",
            json={"upn": "nobody@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 404


def test_enable_archive_rejects_non_super_admin(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _non_admin_context())

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/enable-archive",
            json={"upn": "alice@example.com"},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 403


def test_enable_archive_missing_upn_returns_400(monkeypatch):
    monkeypatch.setattr(main_module, "_load_license_context", _super_admin_context())

    with TestClient(app) as client:
        response = client.post(
            "/m365/mailboxes/enable-archive",
            json={},
            headers=_JSON_HEADERS,
        )

    assert response.status_code == 400
