"""Tests for launching the tray ticket form from a Tactical RMM URL Action."""

from urllib.parse import parse_qs, urlparse

import pytest
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


@pytest.mark.anyio
async def test_url_action_redirects_to_asset_linked_ticket_form(monkeypatch):
    device = {"id": 42, "device_uid": "tray-uid", "asset_id": 7, "status": "active"}

    async def get_device_by_uid(device_uid: str):
        assert device_uid == "tray-uid"
        return device

    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)
    monkeypatch.setattr(tray._settings, "public_base_url", "https://portal.example.com")

    response = await tray.tacticalrmm_ticket_url_action(
        _request(), tray_agent_id="  tray-uid  "
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
        _request(), tray_agent_id="tray-uid"
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/tray/ticket-form/fallback"


@pytest.mark.anyio
async def test_url_action_uses_fallback_when_tray_agent_id_is_missing(monkeypatch):
    async def get_device_by_uid(device_uid: str):
        assert device_uid == ""
        return None

    monkeypatch.setattr(tray.tray_repo, "get_device_by_uid", get_device_by_uid)
    response = await tray.tacticalrmm_ticket_url_action(_request(), tray_agent_id=None)

    assert response.status_code == 303
    assert response.headers["location"] == "/api/tray/ticket-form/fallback"
