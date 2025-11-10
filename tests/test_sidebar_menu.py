import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    async def fake_change_log_sync():
        return None

    async def fake_ensure_modules():
        return None

    async def fake_refresh_automations():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_change_log_sync)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", fake_refresh_automations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def company_admin_context(monkeypatch):
    user = {"id": 1, "email": "admin@example.com", "is_super_admin": False}
    membership = {
        "company_id": 10,
        "is_admin": True,
        "can_access_shop": True,
        "can_access_cart": True,
        "can_access_orders": False,
        "can_access_forms": True,
        "can_manage_assets": False,
        "can_manage_licenses": False,
        "can_manage_invoices": True,
        "can_manage_issues": False,
        "can_manage_staff": False,
        "staff_permission": 2,
    }

    async def fake_require_user(request):
        return user, None

    async def fake_overview(request, current_user):
        return {"unread_notifications": 0}

    async def fake_build_base_context(request, current_user, *, extra=None):
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2025,
            "current_user": current_user,
            "available_companies": [],
            "active_company": None,
            "active_company_id": membership["company_id"],
            "active_membership": membership,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
            "can_access_tickets": True,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)

    yield


def test_company_admin_sees_authorised_menu_items(company_admin_context):
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'href="/tickets"' in html
    assert 'href="/shop"' in html
    assert 'href="/cart"' not in html
    assert 'href="/forms"' in html
    assert 'href="/invoices"' in html
    assert 'href="/staff"' in html
    assert 'href="/orders"' not in html
    assert 'href="/licenses"' not in html
    assert 'href="/assets"' not in html


def test_business_continuity_appears_after_compliance(company_admin_context):
    """Test that Business Continuity menu appears after Compliance in the sidebar."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    
    # Both Compliance and Business Continuity should be present
    assert 'href="/compliance"' in html
    assert 'href="/business-continuity/plans"' in html
    
    # Business Continuity should appear after Compliance
    compliance_pos = html.find('href="/compliance"')
    bc_pos = html.find('href="/business-continuity/plans"')
    
    assert compliance_pos > 0, "Compliance menu not found"
    assert bc_pos > 0, "Business Continuity menu not found"
    assert bc_pos > compliance_pos, "Business Continuity should appear after Compliance in the menu"
    
    # Business Continuity should NOT appear in the Administration section
    admin_section_start = html.find('<li class="menu__heading" role="presentation">Administration</li>')
    if admin_section_start > 0:
        # Verify BC menu appears before the Administration section
        assert bc_pos < admin_section_start, "Business Continuity should not be in Administration section"
