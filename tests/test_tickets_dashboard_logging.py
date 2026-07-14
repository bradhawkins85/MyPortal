from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    monkeypatch.setattr(main_module.tickets_repo, "count_tickets_by_status", AsyncMock(return_value={}))
    monkeypatch.setattr(main_module.tickets_service, "load_dashboard_state", fake_load_dashboard_state)
    monkeypatch.setattr(main_module, "_get_ticket_dashboard_reference_data", fake_get_reference_data)
    monkeypatch.setattr(main_module.labour_types_service, "list_labour_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.site_settings_repo, "get_next_ticket_number", AsyncMock(return_value=1))
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

@pytest.mark.anyio
async def test_hidden_statuses_remain_available_as_dashboard_filters(monkeypatch):
    captured: dict[str, object] = {}
    status_definitions = [
        main_module.tickets_service.TicketStatusDefinition(
            tech_status="open",
            tech_label="Open",
            public_status="Open",
        ),
        main_module.tickets_service.TicketStatusDefinition(
            tech_status="vendor_wait",
            tech_label="Vendor Wait",
            public_status="Waiting",
            hide_from_technicians=True,
            hide_from_admins=True,
        ),
    ]

    async def fake_load_dashboard_state(**kwargs):
        return SimpleNamespace(
            tickets=[],
            total=0,
            status_counts={},
            available_statuses=[definition.tech_status for definition in status_definitions],
            status_definitions=status_definitions,
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
        captured["template_name"] = name
        captured["extra"] = extra or {}
        return HTMLResponse("ok", status_code=status.HTTP_200_OK)

    monkeypatch.setattr(main_module.tickets_service, "load_dashboard_state", fake_load_dashboard_state)
    monkeypatch.setattr(main_module.tickets_repo, "count_tickets_by_status", AsyncMock(return_value={}))
    monkeypatch.setattr(main_module, "_get_ticket_dashboard_reference_data", fake_get_reference_data)
    monkeypatch.setattr(main_module.labour_types_service, "list_labour_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.site_settings_repo, "get_next_ticket_number", AsyncMock(return_value=1))
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)

    request = Request({"type": "http", "method": "GET", "path": "/admin/tickets", "headers": []})

    response = await main_module._render_tickets_dashboard(
        request,
        {"id": 5, "is_super_admin": False},
    )

    assert response.status_code == status.HTTP_200_OK
    assert captured["template_name"] == "admin/tickets.html"
    extra = captured["extra"]
    assert [item["tech_status"] for item in extra["ticket_status_definitions"]] == ["open"]
    assert [item["tech_status"] for item in extra["ticket_filter_status_definitions"]] == [
        "open",
        "vendor_wait",
    ]
