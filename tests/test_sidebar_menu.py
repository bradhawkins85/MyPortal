import pytest
from pathlib import Path
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


def test_m365_menu_item_has_data_menu_key(monkeypatch):
    """Office 365 expandable menu item must have data-menu-key so it appears in left menu customisation."""
    user = {"id": 1, "email": "admin@example.com", "is_super_admin": False}
    membership = {
        "company_id": 10,
        "is_admin": True,
        "can_access_shop": False,
        "can_access_cart": False,
        "can_access_orders": False,
        "can_access_forms": False,
        "can_manage_assets": False,
        "can_manage_licenses": True,
        "can_manage_invoices": False,
        "can_manage_issues": False,
        "can_manage_staff": False,
        "can_view_compliance": False,
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
            "menu_access": main_module._build_menu_access_map(
                is_super_admin=False,
                membership_data=membership,
            ),
            "can_access_tickets": False,
            "can_view_bcp": False,
            "can_view_compliance": False,
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

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'href="/m365"' in html
    assert 'data-menu-key="/m365"' in html, (
        "Office 365 expandable menu item must carry data-menu-key so it is included "
        "in left menu customisation"
    )

def test_profile_menu_permission_shows_my_profile_for_non_admin(monkeypatch):
    user = {"id": 7, "email": "user@example.com", "is_super_admin": False}
    membership = {
        "company_id": 10,
        "is_admin": False,
        "menu_permissions": {"menu.admin.profile": "read", "menu.tickets": "read"},
    }

    async def fake_require_user(request):
        request.state.active_company_id = membership["company_id"]
        request.state.active_membership = membership
        return user, None

    async def fake_overview(request, current_user):
        return {"unread_notifications": 0}

    async def fake_run_system_update(*, force_restart: bool = False):
        return None

    async def fake_build_base_context(request, current_user, *, extra=None):
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2026,
            "current_user": current_user,
            "available_companies": [],
            "active_company": None,
            "active_company_id": membership["company_id"],
            "active_membership": membership,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
            "menu_access": main_module._build_menu_access_map(
                is_super_admin=False,
                membership_data=membership,
            ),
            "can_access_tickets": False,
            "can_view_bcp": False,
            "can_view_compliance": False,
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

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'href="/admin/profile"' in html
    assert "My Profile" in html


def test_non_admin_with_profile_permission_can_open_profile_page(monkeypatch):
    user = {
        "id": 7,
        "email": "user@example.com",
        "is_super_admin": False,
        "mobile_phone": None,
        "booking_link_url": None,
        "matrix_user_id": None,
        "email_signature": None,
    }
    membership = {
        "company_id": 10,
        "is_admin": False,
        "menu_permissions": {"menu.admin.profile": "read", "menu.tickets": "read"},
    }

    async def fake_require_user(request):
        request.state.active_company_id = membership["company_id"]
        request.state.active_membership = membership
        return user, None

    async def fake_get_totp_authenticators(user_id):
        return []

    async def fake_build_base_context(request, current_user, *, extra=None):
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2026,
            "current_user": current_user,
            "available_companies": [],
            "active_company": None,
            "active_company_id": membership["company_id"],
            "active_membership": membership,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
            "menu_access": main_module._build_menu_access_map(
                is_super_admin=False,
                membership_data=membership,
            ),
            "is_super_admin": False,
            "is_helpdesk_technician": True,
            "matrix_chat_enabled": False,
            "plausible_config": {"enabled": False, "site_domain": "", "base_url": ""},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module.auth_repo, "get_totp_authenticators", fake_get_totp_authenticators)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    main_module.templates.env.globals["plausible_config"] = {
        "enabled": False,
        "site_domain": "",
        "base_url": "",
    }

    with TestClient(app) as client:
        response = client.get("/admin/profile")

    assert response.status_code == 200
    assert "Manage your account security" in response.text
    assert response.text.count("rich_text_editor.js") == 0
    assert "Notification Contact" not in response.text
    assert "Booking Link" not in response.text
    assert "Matrix Username" not in response.text
    assert "Email signature" not in response.text
    assert "profile-columns--standard" in response.text


def test_technician_with_profile_permission_keeps_profile_contact_tools(monkeypatch):
    user = {
        "id": 8,
        "email": "tech@example.com",
        "is_super_admin": False,
        "mobile_phone": None,
        "booking_link_url": None,
        "matrix_user_id": None,
        "email_signature": None,
    }
    membership = {
        "company_id": 10,
        "is_admin": False,
        "menu_permissions": {"menu.admin.profile": "read", "menu.tickets": "write"},
    }

    async def fake_require_user(request):
        request.state.active_company_id = membership["company_id"]
        request.state.active_membership = membership
        return user, None

    async def fake_get_totp_authenticators(user_id):
        return []

    async def fake_build_base_context(request, current_user, *, extra=None):
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2026,
            "current_user": current_user,
            "available_companies": [],
            "active_company": None,
            "active_company_id": membership["company_id"],
            "active_membership": membership,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
            "menu_access": main_module._build_menu_access_map(
                is_super_admin=False,
                membership_data=membership,
                is_helpdesk_technician=True,
            ),
            "is_super_admin": False,
            "is_helpdesk_technician": True,
            "matrix_chat_enabled": True,
            "plausible_config": {"enabled": False, "site_domain": "", "base_url": ""},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module.auth_repo, "get_totp_authenticators", fake_get_totp_authenticators)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    main_module.templates.env.globals["plausible_config"] = {
        "enabled": False,
        "site_domain": "",
        "base_url": "",
    }

    with TestClient(app) as client:
        response = client.get("/admin/profile")

    assert response.status_code == 200
    assert "Notification Contact" in response.text
    assert "Booking Link" in response.text
    assert "Matrix Username" in response.text
    assert "Email signature" in response.text
    assert response.text.count("rich_text_editor.js") == 1
    assert "profile-columns--standard" not in response.text


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


def test_help_and_reports_are_not_baseline_authenticated_menu_items():
    menu_access = main_module._build_menu_access_map(
        is_super_admin=False,
        membership_data={},
    )

    assert menu_access["menu.dashboard"] == "read"
    assert menu_access["menu.knowledge_base"] == "read"
    assert menu_access["menu.help"] == "none"
    assert menu_access["menu.reports"] == "none"


def test_help_and_reports_menu_items_require_explicit_permission():
    menu_access = main_module._build_menu_access_map(
        is_super_admin=False,
        membership_data={
            "menu_permissions": {
                "menu.help": "read",
                "menu.reports": "read",
            }
        },
    )

    assert menu_access["menu.help"] == "read"
    assert menu_access["menu.reports"] == "read"


def test_super_admin_keeps_help_and_reports_menu_access():
    menu_access = main_module._build_menu_access_map(
        is_super_admin=True,
        membership_data={},
    )

    assert menu_access["menu.help"] == "write"
    assert menu_access["menu.reports"] == "write"


def test_backup_summary_menu_permission_shows_admin_menu_without_super_admin():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["static_url"] = lambda path: path
    template = env.get_template("base.html")
    html = template.render(
        request=type("Request", (), {"url": type("Url", (), {"path": "/admin/backup-summary"})()})(),
        current_user={"id": 2, "is_super_admin": False},
        active_membership={},
        menu_access={"menu.admin.backup_summary": "read"},
        csrf_token=None,
        available_companies=[],
        app_name="MyPortal",
        current_year=2026,
        cart_summary={"item_count": 0, "total_quantity": 0},
        plausible_config={"enabled": False},
        static_url=lambda path: path,
    )

    assert "Administration" in html
    assert 'href="/admin/backup-summary"' in html
    assert "Backup Summary" in html
    assert 'href="/admin/backup-jobs"' not in html
    assert 'href="/admin/impersonation"' not in html


def test_staff_menu_no_access_overrides_staff_assignment_levels():
    for staff_permission in (0, 1, 3):
        menu_access = main_module._build_menu_access_map(
            is_super_admin=False,
            membership_data={
                "menu_permissions": {"menu.staff": "none"},
                "staff_permission": staff_permission,
                "can_manage_staff": True,
            },
        )

        assert menu_access["menu.staff"] == "none"

    nested_menu_access = main_module._build_menu_access_map(
        is_super_admin=False,
        membership_data={
            "menu_permissions": {
                "menu": {"menu.staff": "No Access"},
                "permissions": ["staff.manage"],
            },
            "staff_permission": 3,
            "can_manage_staff": True,
        },
    )

    assert nested_menu_access["menu.staff"] == "none"


def test_staff_link_hidden_when_staff_menu_no_access_even_with_staff_assignment():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["static_url"] = lambda path: path
    template = env.get_template("base.html")
    html = template.render(
        request=type("Request", (), {"url": type("Url", (), {"path": "/"})()})(),
        current_user={"id": 2, "is_super_admin": False},
        active_membership={"staff_permission": 3, "can_manage_staff": True},
        staff_permission=3,
        can_manage_staff=True,
        menu_access={
            "menu.dashboard": "read",
            "menu.staff": "none",
        },
        available_companies=[],
        cart_summary={"item_count": 0, "total_quantity": 0, "subtotal": 0},
        notification_unread_count=0,
        plausible_config={"enabled": False},
        csrf_token="csrf-token",
    )

    assert 'href="/staff"' not in html


def test_tickets_menu_permission_uses_no_access_own_all_levels():
    no_access = main_module._build_menu_access_map(
        is_super_admin=False,
        membership_data={},
        is_helpdesk_technician=False,
    )
    own_access = main_module._build_menu_access_map(
        is_super_admin=False,
        membership_data={"menu_permissions": {"menu.tickets": "read"}},
        is_helpdesk_technician=False,
    )
    all_access = main_module._build_menu_access_map(
        is_super_admin=False,
        membership_data={"menu_permissions": {"menu.tickets": "write"}},
        is_helpdesk_technician=True,
    )

    assert no_access["menu.tickets"] == "none"
    assert own_access["menu.tickets"] == "read"
    assert all_access["menu.tickets"] == "write"


def test_tickets_role_ui_labels_are_no_access_own_all():
    template = Path("app/templates/admin/roles.html").read_text()

    assert "permission.key == 'menu.tickets'" in template
    assert "{{ 'Own' if is_ticket_permission else 'Read Only' }}" in template
    assert "{{ 'All' if is_ticket_permission else ('Yes' if is_boolean_permission else 'Read/Write') }}" in template
    assert "All opens <code>/tickets</code> for company tickets" in template
