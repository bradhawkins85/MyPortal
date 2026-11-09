"""Tests for launching the tray ticket form from a Tactical RMM URL Action."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import tray


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("portal.example.com", 443),
            "path": "/api/tray/ticket-form/url-action",
            "query_string": b"",
            "headers": [],
        }
    )


TECHNICIAN = {"id": 1, "is_super_admin": True}


@pytest.mark.anyio
async def test_url_action_redirects_to_asset_linked_ticket_form(monkeypatch):
    device = {"id": 42, "device_uid": "tray-uid", "asset_id": 7, "status": "active"}

    async def get_device_by_uid(device_uid: str):
        assert device_uid == "tray-uid"
        return device

    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)
    monkeypatch.setattr(tray._settings, "public_base_url", "https://portal.example.com")

    response = await tray.tacticalrmm_ticket_url_action(
        _request(), tray_agent_id="  tray-uid  ", current_user=TECHNICIAN
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert urlparse(location).path == "/api/tray/ticket-form"
    session = tray._parse_ticket_form_token(
        parse_qs(urlparse(location).query)["token"][0]
    )
    assert session is not None
    assert session["device_id"] == 42
    assert session["mode"] == "myportal"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "device",
    [
        None,
        {"id": 42, "asset_id": 7, "status": "revoked"},
        {"id": 42, "asset_id": None, "status": "active"},
    ],
)
async def test_url_action_uses_fallback_for_unavailable_or_unlinked_devices(
    monkeypatch, device
):
    async def get_device_by_uid(_device_uid: str):
        return device

    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)

    response = await tray.tacticalrmm_ticket_url_action(
        _request(), tray_agent_id="tray-uid", current_user=TECHNICIAN
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/tray/ticket-form/fallback"


@pytest.mark.anyio
async def test_url_action_uses_fallback_when_tray_agent_id_is_missing(monkeypatch):
    async def get_device_by_uid(device_uid: str):
        assert device_uid == ""
        return None

    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)
    response = await tray.tacticalrmm_ticket_url_action(
        _request(), tray_agent_id=None, current_user=TECHNICIAN
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/tray/ticket-form/fallback"


@pytest.mark.anyio
async def test_url_action_sends_anonymous_callers_to_login(monkeypatch):
    async def get_device_by_uid(_device_uid: str):
        raise AssertionError("an anonymous caller must not reach the device lookup")

    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)

    response = await tray.tacticalrmm_ticket_url_action(
        _request(), tray_agent_id="tray uid", current_user=None
    )

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"] == [
        "/api/tray/ticket-form/url-action?TrayAgentID=tray%20uid"
    ]


@pytest.mark.anyio
async def test_url_action_refuses_users_without_technician_access(monkeypatch):
    async def user_has_permission(_user_id: int, _key: str) -> bool:
        return False

    async def get_device_by_uid(_device_uid: str):
        raise AssertionError("a non-technician must not reach the device lookup")

    from app.api.dependencies import auth as auth_dependencies

    monkeypatch.setattr(auth_dependencies.membership_repo, "user_has_permission", user_has_permission)
    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)

    with pytest.raises(HTTPException) as excinfo:
        await tray.tacticalrmm_ticket_url_action(
            _request(), tray_agent_id="tray-uid", current_user={"id": 5, "is_super_admin": False}
        )
    assert excinfo.value.status_code == 403
