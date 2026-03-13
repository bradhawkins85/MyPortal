from types import SimpleNamespace

import pytest
from fastapi import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main as main_module


@pytest.mark.anyio
async def test_phone_search_error_does_not_log_raw_phone(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_list_tickets_by_requester_phone(phone_number: str, limit: int = 100):
        raise RuntimeError("boom")

    async def fake_load_dashboard_state(
        status_filter=None,
        module_filter=None,
        limit=200,
        include_reference_data=False,
    ):
        return SimpleNamespace(
            tickets=[],
            total=0,
            status_counts={},
            available_statuses=[],
            status_definitions=[],
            modules=[],
            companies=[],
            technicians=[],
            company_lookup={},
            user_lookup={},
        )

    async def fake_get_reference_data():
        return {
            "modules": [],
            "companies": [],
            "technicians": [],
            "company_lookup": {},
            "user_lookup": {},
        }

    async def fake_render_template(name, request, user, extra=None):
        return HTMLResponse("ok", status_code=status.HTTP_200_OK)

    def fake_log_error(message, **meta):
        captured["message"] = message
        captured["meta"] = meta

    monkeypatch.setattr(main_module.tickets_repo, "list_tickets_by_requester_phone", fake_list_tickets_by_requester_phone)
    monkeypatch.setattr(main_module.tickets_service, "load_dashboard_state", fake_load_dashboard_state)
    monkeypatch.setattr(main_module, "_get_ticket_dashboard_reference_data", fake_get_reference_data)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)
    monkeypatch.setattr(main_module, "log_error", fake_log_error)

    request = Request({"type": "http", "method": "GET", "path": "/admin/tickets", "headers": []})

    response = await main_module._render_tickets_dashboard(
        request,
        {"id": 5, "is_super_admin": False},
        phone_number="123\nforged",
    )

    assert response.status_code == status.HTTP_200_OK
    assert captured["message"] == "Error searching tickets by phone number"
    assert captured["meta"]["phone_number_provided"] is True
    assert "phone_number" not in captured["meta"]
