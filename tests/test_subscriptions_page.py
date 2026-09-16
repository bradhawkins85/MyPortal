"""Tests for the subscriptions page view."""
from datetime import date, timedelta

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

    async def fake_run_system_update(*, force_restart: bool = False):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_change_log_sync)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", fake_refresh_automations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(scheduler_service, "run_system_update", fake_run_system_update)


@pytest.fixture
def authorized_user_context(monkeypatch):
    """User with both can_manage_licenses and can_access_cart permissions."""
    user = {"id": 1, "email": "admin@example.com", "is_super_admin": False, "company_id": 10}
    membership = {
        "company_id": 10,
        "is_admin": True,
        "can_manage_licenses": True,
        "can_access_cart": True,
    }
    company = {"id": 10, "name": "Test Company"}

    async def fake_require_user(request):
        return user, None

    async def fake_get_user_company(user_id, company_id):
        return membership

    async def fake_get_company(company_id):
        return company

    async def fake_list_subscriptions(**kwargs):
        return [
            {
                "id": "sub-1",
                "customer_id": 10,
                "product_id": 1,
                "product_name": "Product A",
                "subscription_category_id": 1,
                "category_name": "Software",
                "start_date": date(2025, 1, 1),
                "end_date": date.today() + timedelta(days=14),
                "quantity": 5,
                "unit_price": "10.00",
                "prorated_price": None,
                "status": "active",
                "auto_renew": False,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_pending_changes(subscription_ids):
        return {"sub-1": [{"id": "chg-1", "change_type": "decrease", "quantity_change": 1}]}

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
            "plausible_config": {"enabled": False},
            "module_enabled": {},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    monkeypatch.setattr(main_module.user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(main_module.company_repo, "get_company_by_id", fake_get_company)

    # Mock the subscriptions repository
    from app.repositories import subscriptions as subscriptions_repo
    from app.repositories import subscription_change_requests as change_requests_repo
    monkeypatch.setattr(subscriptions_repo, "list_subscriptions", fake_list_subscriptions)
    monkeypatch.setattr(
        change_requests_repo,
        "list_pending_changes_for_subscriptions",
        fake_pending_changes,
    )

    yield


@pytest.fixture
def super_admin_user_context(monkeypatch):
    """Super admin user context."""
    user = {"id": 1, "email": "superadmin@example.com", "is_super_admin": True, "company_id": 10}
    membership = {
        "company_id": 10,
        "is_admin": True,
        "can_manage_licenses": True,
        "can_access_cart": True,
    }
    company = {"id": 10, "name": "Test Company"}

    async def fake_require_user(request):
        return user, None

    async def fake_get_user_company(user_id, company_id):
        return membership

    async def fake_get_company(company_id):
        return company

    async def fake_list_subscriptions(**kwargs):
        return [
            {
                "id": "sub-1",
                "customer_id": 10,
                "product_id": 1,
                "product_name": "Product A",
                "subscription_category_id": 1,
                "category_name": "Software",
                "start_date": date(2025, 1, 1),
                "end_date": date.today() + timedelta(days=14),
                "quantity": 5,
                "unit_price": "10.00",
                "prorated_price": None,
                "status": "active",
                "auto_renew": False,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_pending_changes(subscription_ids):
        return {"sub-1": [{"id": "chg-1", "change_type": "decrease", "quantity_change": 1}]}

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
            "plausible_config": {"enabled": False},
            "module_enabled": {},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    monkeypatch.setattr(main_module.user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(main_module.company_repo, "get_company_by_id", fake_get_company)

    # Mock the subscriptions repository
    from app.repositories import subscriptions as subscriptions_repo
    from app.repositories import subscription_change_requests as change_requests_repo
    monkeypatch.setattr(subscriptions_repo, "list_subscriptions", fake_list_subscriptions)
    monkeypatch.setattr(
        change_requests_repo,
        "list_pending_changes_for_subscriptions",
        fake_pending_changes,
    )

    yield


@pytest.fixture
def unauthorized_user_context(monkeypatch):
    """User without required permissions."""
    user = {"id": 2, "email": "user@example.com", "is_super_admin": False, "company_id": 10}
    membership = {
        "company_id": 10,
        "is_admin": False,
        "can_manage_licenses": False,
        "can_access_cart": False,
    }

    async def fake_require_user(request):
        return user, None

    async def fake_get_user_company(user_id, company_id):
        return membership

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
            "plausible_config": {"enabled": False},
            "module_enabled": {},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    monkeypatch.setattr(main_module.user_company_repo, "get_user_company", fake_get_user_company)

    yield


def test_subscriptions_page_requires_both_permissions(authorized_user_context):
    """Test that subscriptions page renders with proper permissions."""
    with TestClient(app) as client:
        response = client.get("/subscriptions")

    assert response.status_code == 200
    html = response.text
    assert "Active subscriptions" in html
    assert "Product A" in html
    # Check for the new button text (we changed from "Request change" to "Add licenses" and "Request decrease")
    assert ("Add licenses" in html or "Request decrease" in html)


def test_subscriptions_page_shows_dollar_sign_for_unit_price(authorized_user_context):
    """Test that subscriptions page displays unit price with dollar sign, not pound or euro symbols."""
    with TestClient(app) as client:
        response = client.get("/subscriptions")

    assert response.status_code == 200
    html = response.text
    # Check that the unit price is displayed with a dollar sign
    assert "$10.00" in html or ">$10.00<" in html
    # Ensure pound and euro symbols are not used for unit price
    assert "£10.00" not in html
    assert "€10.00" not in html


def test_subscriptions_page_redirects_without_permissions(unauthorized_user_context):
    """Test that subscriptions page redirects users without permissions."""
    with TestClient(app) as client:
        response = client.get("/subscriptions", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_subscriptions_menu_item_shown_with_permissions(authorized_user_context, monkeypatch):
    """Test that subscriptions menu item appears for authorized users."""
    # Mock database calls needed for home page
    async def fake_overview(request, current_user):
        return {"unread_notifications": 0}
    
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'href="/subscriptions"' in html
    assert "Subscriptions" in html


def test_subscriptions_menu_item_hidden_without_permissions(unauthorized_user_context, monkeypatch):
    """Test that subscriptions menu item is hidden for unauthorized users."""
    # Mock database calls needed for home page
    async def fake_overview(request, current_user):
        return {"unread_notifications": 0}
    
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'href="/subscriptions"' not in html


def test_subscriptions_page_shows_delete_button_for_super_admin(super_admin_user_context):
    """Test that super admins see a delete button on the subscriptions page."""
    with TestClient(app) as client:
        response = client.get("/subscriptions")

    assert response.status_code == 200
    html = response.text
    assert 'data-subscription-delete="sub-1"' in html
    assert "Delete" in html


def test_subscriptions_page_hides_delete_button_for_regular_admin(authorized_user_context):
    """Test that regular admins do not see a delete button on the subscriptions page."""
    with TestClient(app) as client:
        response = client.get("/subscriptions")

    assert response.status_code == 200
    html = response.text
    assert 'data-subscription-delete=' not in html


def test_subscriptions_page_shows_forecast_and_risk_summary(authorized_user_context):
    with TestClient(app) as client:
        response = client.get("/subscriptions")

    assert response.status_code == 200
    html = response.text
    assert "Renewal forecast" in html
    assert "Projected 90-day renewal revenue" in html
    assert "Accounts needing attention" in html
    assert 'class="stat-strip"' in html
    assert html.count('class="stat-strip__stat ') == 4
    assert 'class="stat-cards"' not in html
    assert "Risk: High" in html
    assert "Pending decrease requests indicate a planned contraction." in html
