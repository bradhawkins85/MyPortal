"""Tray menu items of disabled integrations are replaced by MyPortal's own."""
from __future__ import annotations

import pytest

from app.services import asset_presence
from app.services import tray as tray_service

MENU = [
    {"type": "submit_syncro_ticket", "label": ""},
    {"type": "TRMM_Script", "label": "Fix printer", "script_id": 4},
    {"type": "submenu", "label": "Tools", "children": [
        {"type": "trmm_script", "label": "Restart", "script_id": 5},
    ]},
    {"type": "submenu", "label": "Help", "children": [
        {"type": "submit_syncro_ticket", "label": "Ask IT"},
        {"type": "rmm_script", "label": "Clear cache", "script_id": 9},
    ]},
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _modules(monkeypatch, **enabled):
    async def fake(slug):
        return enabled.get(slug, False)

    monkeypatch.setattr(asset_presence, "module_enabled", fake)


@pytest.mark.anyio
async def test_disabled_integrations_are_replaced(monkeypatch):
    _modules(monkeypatch)

    menu = await tray_service._replace_disabled_integration_items(MENU)

    assert menu == [
        {"type": "submit_ticket", "label": "Submit Ticket"},
        {"type": "submenu", "label": "Help", "children": [
            {"type": "submit_ticket", "label": "Ask IT"},
            {"type": "rmm_script", "label": "Clear cache", "script_id": 9},
        ]},
    ]


@pytest.mark.anyio
async def test_enabled_integrations_are_kept(monkeypatch):
    _modules(monkeypatch, tacticalrmm=True, syncro=True)

    assert await tray_service._replace_disabled_integration_items(MENU) == MENU


@pytest.mark.anyio
@pytest.mark.parametrize("syncro_on, expected", [(False, "myportal"), (True, "syncro")])
async def test_syncro_ticket_token_falls_back_to_myportal(monkeypatch, syncro_on, expected):
    from unittest.mock import AsyncMock, MagicMock

    from starlette.requests import Request

    from app.api.routes import tray as tray_routes

    _modules(monkeypatch, syncro=syncro_on)
    issue = MagicMock(return_value=("token", "csrf"))
    monkeypatch.setattr(tray_routes, "_issue_ticket_form_token", issue)
    monkeypatch.setattr(tray_routes, "_ticket_form_url", lambda token, request: "https://portal/form")
    request = Request({"type": "http", "method": "POST", "path": "/api/tray/ticket-token", "headers": []})
    monkeypatch.setattr(request, "json", AsyncMock(return_value={"mode": "syncro"}))

    await tray_routes.issue_ticket_token(request, device={"id": 3})

    assert issue.call_args.args[1] == expected
