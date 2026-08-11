import asyncio
import json
from types import SimpleNamespace

from app.features.staff import handlers
from app.services.tickets import TicketDashboardState


def async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def test_staff_page_menu_read_only_disables_staff_editing(monkeypatch):
    captured = {}

    async def fake_load_staff_context(request):
        return (
            {"id": 10, "company_id": 1, "is_super_admin": False},
            {"menu_permissions": {"menu.staff": "read"}, "staff_permission": 3, "can_manage_staff": True},
            {"id": 1, "email_domains": []},
            3,
            1,
            None,
        )

    async def fake_render_template(template_name, request, user, *, extra):
        captured.update(extra)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    monkeypatch.setattr(handlers.main_module, "_is_helpdesk_technician", async_return(False))
    monkeypatch.setattr(handlers.main_module, "_has_admin_technician_access", async_return(False))
    monkeypatch.setattr(handlers.staff_field_config_service, "load_effective_company_staff_fields", async_return([]))
    monkeypatch.setattr(handlers.staff_custom_fields_repo, "list_field_definitions", async_return([]))
    monkeypatch.setattr(handlers.staff_repo, "list_staff", async_return([]))
    monkeypatch.setattr(handlers.company_repo, "get_company_by_id", async_return({"email_domains": []}))
    monkeypatch.setattr(handlers.staff_repo, "list_active_staff_for_offboarding", async_return([]))
    monkeypatch.setattr(handlers.m365_service, "get_credentials", async_return(None))
    monkeypatch.setattr(handlers.staff_workflow_repo, "list_executions_for_staff_ids", async_return({}))
    monkeypatch.setattr(handlers, "_render_template", fake_render_template)

    asyncio.run(handlers.staff_page(SimpleNamespace()))

    assert captured["can_edit_staff"] is False
    assert captured["can_request_staff_offboarding"] is True
    assert captured["can_approve_onboarding"] is False


def test_staff_page_technician_can_approve_without_staff_manage(monkeypatch):
    captured = {}

    async def fake_load_staff_context(request):
        return (
            {"id": 11, "company_id": 1, "is_super_admin": False},
            {"menu_permissions": {"menu.staff": "write"}, "staff_permission": 0},
            {"id": 1, "email_domains": []},
            0,
            1,
            None,
        )

    async def fake_render_template(template_name, request, user, *, extra):
        captured.update(extra)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    monkeypatch.setattr(handlers.main_module, "_is_helpdesk_technician", async_return(True))
    monkeypatch.setattr(handlers.main_module, "_has_admin_technician_access", async_return(True))
    monkeypatch.setattr(handlers.staff_field_config_service, "load_effective_company_staff_fields", async_return([]))
    monkeypatch.setattr(handlers.staff_custom_fields_repo, "list_field_definitions", async_return([]))
    monkeypatch.setattr(handlers.staff_repo, "list_staff", async_return([]))
    monkeypatch.setattr(handlers.company_repo, "get_company_by_id", async_return({"email_domains": []}))
    monkeypatch.setattr(handlers.staff_repo, "list_active_staff_for_offboarding", async_return([]))
    monkeypatch.setattr(handlers.m365_service, "get_credentials", async_return(None))
    monkeypatch.setattr(handlers.staff_requests_repo, "list_requests", async_return([{"id": 1}]))
    monkeypatch.setattr(handlers.staff_workflow_repo, "list_executions_for_staff_ids", async_return({}))
    monkeypatch.setattr(handlers, "_render_template", fake_render_template)

    asyncio.run(handlers.staff_page(SimpleNamespace()))

    assert captured["can_edit_staff"] is True
    assert captured["can_request_staff_offboarding"] is True
    assert captured["can_approve_onboarding"] is True
    assert captured["staff_pending_requests"] == [{"id": 1}]
    assert captured["has_admin_technician_access"] is True


def test_staff_page_template_exposes_offboarding_actions_for_read_access():
    template = open("app/templates/staff/index.html", encoding="utf-8").read()

    assert "can_edit_staff or can_request_staff_offboarding" in template
    assert "'canRequestStaffOffboarding': can_request_staff_offboarding" in template
    assert "{{ 'Edit' if can_edit_staff else 'Actions' }}" in template


def test_staff_js_uses_offboarding_permission_flag():
    script = open("app/static/js/staff.js", encoding="utf-8").read()

    assert "flags.canRequestStaffOffboarding" in script
    assert "!staffId || !(flags && flags.canEditStaff)" in script


def test_staff_ticket_modal_is_restricted_to_admin_technicians():
    template = open("app/templates/staff/index.html", encoding="utf-8").read()
    script = open("app/static/js/staff.js", encoding="utf-8").read()

    assert "{% if has_admin_technician_access %}" in template
    assert 'data-staff-tickets="{{ member.id }}"' in template
    assert 'id="staff-tickets-modal"' in template
    assert "/api/staff/${encodeURIComponent(staffId)}/tickets" in script


def test_staff_member_tickets_returns_company_scoped_requester_tickets(monkeypatch):
    request = SimpleNamespace()
    dashboard = TicketDashboardState(
        tickets=[{"id": 23, "subject": "Printer", "status": "open", "priority": "high", "company_id": 7}],
        total=1,
        status_counts={},
        available_statuses=[],
        status_definitions=[],
        modules=[],
        companies=[],
        technicians=[],
        company_lookup={7: {"name": "Example Co"}},
        user_lookup={},
    )
    monkeypatch.setattr(handlers.main_module, "_require_authenticated_user", async_return(({"id": 1, "company_id": 7}, None)))
    monkeypatch.setattr(handlers.main_module, "_has_admin_technician_access", async_return(True))
    monkeypatch.setattr(handlers.staff_repo, "get_staff_by_id", async_return({"id": 5, "company_id": 7, "first_name": "Alex", "last_name": "Smith"}))
    monkeypatch.setattr(handlers.tickets_service, "load_dashboard_state", async_return(dashboard))

    response = asyncio.run(handlers.staff_member_tickets(5, request))
    payload = json.loads(response.body)

    assert payload["staffName"] == "Alex Smith"
    assert payload["tickets"][0] == {
        "id": 23,
        "subject": "Printer",
        "status": "Open",
        "priority": "high",
        "company": "Example Co",
        "assigned": "—",
        "updatedAt": None,
    }
