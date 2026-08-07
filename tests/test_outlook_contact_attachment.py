import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main
from app.main import app


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


def test_requester_mobile_attachment_route_is_available():
    routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/tickets/{ticket_id}/requester/mobile"
    ]

    assert len(routes) == 1
    assert "POST" in routes[0].methods


@pytest.mark.anyio
async def test_attach_requester_mobile_updates_the_tickets_staff_record(monkeypatch):
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=({"id": 7}, None)))
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value={"requester_staff_id": 42}))
    monkeypatch.setattr(main.staff_repo, "get_staff_by_id", AsyncMock(return_value={"id": 42}))
    update_mobile = AsyncMock()
    monkeypatch.setattr(main.staff_repo, "update_mobile_phone", update_mobile)

    response = await main.attach_ticket_requester_mobile(
        _json_request({"phone": "+61 400 111 222"}),
        123,
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "ok": True,
        "staff_id": 42,
        "mobile_phone": "+61 400 111 222",
    }
    update_mobile.assert_awaited_once_with(42, "+61 400 111 222")


def test_outlook_results_use_portal_click_to_call_and_offer_attachment():
    script = Path("app/static/js/ticket_detail.js").read_text()

    assert "window.__portalClickToCall" in script
    assert "Attach to contact" in script
    assert "link.href = `tel:" not in script
