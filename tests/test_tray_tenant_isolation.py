"""Cross-tenant isolation checks for tray enrolment and popup chat rooms."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_room_accessible_to_device_requires_same_company_and_device():
    from app.api.routes.tray import room_accessible_to_device

    device = {"id": 5, "company_id": 10}
    assert room_accessible_to_device({"company_id": 10, "tray_device_id": 5}, device)
    assert room_accessible_to_device({"company_id": 10, "tray_device_id": None}, device)
    assert not room_accessible_to_device({"company_id": 11, "tray_device_id": None}, device)
    assert not room_accessible_to_device({"company_id": 10, "tray_device_id": 6}, device)
    assert not room_accessible_to_device(None, device)
    assert not room_accessible_to_device({"company_id": 10}, {"id": 5, "company_id": None})


@pytest.mark.anyio
async def test_issue_chat_token_rejects_room_from_other_company(monkeypatch):
    from app.api.routes import tray as tray_routes

    async def fake_get_company_by_id(company_id):
        return {"id": company_id, "tray_chat_enabled": True}

    async def fake_get_room(room_id):
        return {"id": room_id, "company_id": 99, "status": "open", "tray_device_id": None}

    async def fail_create_chat_token(**kwargs):
        raise AssertionError("token must not be issued")

    class _Request:
        async def json(self):
            return {"room_id": 42}

    monkeypatch.setattr(tray_routes.companies_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(tray_routes.chat_repo, "get_room", fake_get_room)
    monkeypatch.setattr(tray_routes.tray_repo, "create_chat_token", fail_create_chat_token)
    monkeypatch.setattr(tray_routes._settings, "matrix_enabled", True, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await tray_routes.issue_chat_token(_Request(), device={"id": 1, "company_id": 10})  # type: ignore[arg-type]
    assert exc_info.value.status_code == 404


def _enrol_stubs(monkeypatch, existing):
    from app.api.routes import tray as tray_routes

    async def fake_get_install_token_by_hash(token_hash):
        return {"id": 3, "company_id": 10, "revoked_at": None, "expires_at": None}

    async def fake_find_matching_asset(**kwargs):
        return None

    async def fake_get_device_by_uid(device_uid):
        return existing

    async def fail_update_device_auth(*args, **kwargs):
        raise AssertionError("device token must not be rotated")

    monkeypatch.setattr(tray_routes.tray_repo, "get_install_token_by_hash", fake_get_install_token_by_hash)
    monkeypatch.setattr(tray_routes.tray_service, "find_matching_asset", fake_find_matching_asset)
    monkeypatch.setattr(tray_routes.tray_repo, "get_device_by_uid", fake_get_device_by_uid)
    monkeypatch.setattr(tray_routes.tray_repo, "update_device_auth", fail_update_device_auth)
    return tray_routes


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("existing", "expected_status"),
    [
        ({"id": 7, "company_id": 20, "status": "active"}, 409),
        ({"id": 7, "company_id": 10, "status": "revoked"}, 403),
    ],
)
async def test_enrol_refuses_foreign_or_revoked_device(monkeypatch, existing, expected_status):
    from app.schemas.tray import TrayEnrolRequest

    tray_routes = _enrol_stubs(monkeypatch, existing)
    payload = TrayEnrolRequest(install_token="install-token-value", device_uid="device-abc", os="windows")
    with pytest.raises(HTTPException) as exc_info:
        await tray_routes.enrol_device(payload, SimpleNamespace(headers={}))  # type: ignore[arg-type]
    assert exc_info.value.status_code == expected_status
