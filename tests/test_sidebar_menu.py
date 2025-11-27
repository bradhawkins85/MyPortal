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

    async def fake_get_module(slug, *, redact=True):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_change_log_sync)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main_module.modules_service, "get_module", fake_get_module)
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
        "can_view_compliance": True,
        "staff_permission": 2,
    }

    async def fake_require_user(request):
        return user, None

    async def fake_overview(request, current_user):
        return {"unread_notifications": 0}

    async def fake_run_system_update(*, force_restart: bool = False):
        return None

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
            "can_view_bcp": True,
            "can_view_compliance": True,
            "plausible_config": {"enabled": False, "site_domain": "", "base_url": ""},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    monkeypatch.setattr(scheduler_service, "run_system_update", fake_run_system_update)
    main_module.templates.env.globals["plausible_config"] = {
        "enabled": False,
        "site_domain": "",
        "base_url": "",
    }

    yield


def test_company_admin_sees_authorised_menu_items(company_admin_context):
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'href="/tickets"' in html
    assert 'href="/shop"' in html
    assert 'href="/cart"' not in html
    assert 'href="/myforms"' in html
    assert 'href="/invoices"' in html
    assert 'href="/staff"' in html
    assert 'href="/orders"' not in html
    assert 'href="/licenses"' not in html
    assert 'href="/assets"' not in html


def test_bcp_menu_replaces_business_continuity(company_admin_context):
    """Ensure the BCP navigation link is present and the legacy menu is removed."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text

    # Compliance remains available and BCP has replaced the legacy menu
    assert 'href="/compliance"' in html
    assert 'href="/bcp"' in html
    assert '/business-continuity/' not in html

    # BCP should appear after Compliance in the menu ordering
    compliance_pos = html.find('href="/compliance"')
    bcp_pos = html.find('href="/bcp"')

    assert compliance_pos > 0, "Compliance menu not found"
    assert bcp_pos > 0, "BCP menu not found"
    assert bcp_pos > compliance_pos, "BCP should appear after Compliance in the menu"


@pytest.fixture
def public_knowledge_base_context(monkeypatch):
    """Fixture to simulate an unauthenticated user viewing the knowledge base."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeArticleAccessContext:
        user: None = None
        user_id: None = None
        is_super_admin: bool = False
        memberships: dict = field(default_factory=dict)

    async def fake_get_optional_user(request):
        return None, None

    async def fake_list_articles_for_context(context, *, include_unpublished=False):
        return []

    async def fake_build_access_context(user):
        return FakeArticleAccessContext()

    async def fake_build_public_context(request, *, extra=None):
        from decimal import Decimal
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2025,
            "user": None,
            "current_user": None,
            "available_companies": [],
            "active_company": None,
            "active_company_id": None,
            "active_membership": None,
            "csrf_token": None,
            "staff_permission": 0,
            "is_super_admin": False,
            "is_helpdesk_technician": False,
            "is_company_admin": False,
            "can_access_shop": False,
            "can_access_cart": False,
            "can_access_orders": False,
            "can_access_forms": False,
            "can_manage_assets": False,
            "can_manage_licenses": False,
            "can_manage_invoices": False,
            "can_manage_staff": False,
            "can_view_compliance": False,
            "plausible_config": {"enabled": False},
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")},
            "notification_unread_count": 0,
            "enable_auto_refresh": False,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_get_optional_user", fake_get_optional_user)
    monkeypatch.setattr(
        main_module.knowledge_base_service,
        "list_articles_for_context",
        fake_list_articles_for_context,
    )
    monkeypatch.setattr(
        main_module.knowledge_base_service,
        "build_access_context",
        fake_build_access_context,
    )
    monkeypatch.setattr(main_module, "_build_public_context", fake_build_public_context)

    yield


def test_public_knowledge_base_shows_login_menu(public_knowledge_base_context):
    """Ensure unauthenticated users see the sign in and knowledge base menu on /knowledge-base."""
    with TestClient(app) as client:
        response = client.get("/knowledge-base")

    assert response.status_code == 200
    html = response.text

    # Should show the Sign in link
    assert 'href="/login"' in html
    assert "Sign in" in html

    # Should show the Knowledge base link
    assert 'href="/knowledge-base"' in html
    assert "Knowledge base" in html
