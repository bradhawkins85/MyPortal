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
    
    # Clear Jinja2 template cache to ensure fresh templates are loaded
    main_module.templates.env.cache.clear()
    
    yield


@pytest.fixture
def single_company_context(monkeypatch):
    """Context for a user with only one company."""
    user = {"id": 1, "email": "user@example.com", "is_super_admin": False}
    membership = {
        "company_id": 10,
        "is_admin": False,
        "can_access_shop": True,
        "can_access_cart": True,
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
            "available_companies": [
                {"company_id": 10, "company_name": "Single Company Inc."}
            ],
            "active_company": {"company_id": 10, "company_name": "Single Company Inc."},
            "active_company_id": membership["company_id"],
            "active_membership": membership,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)

    yield


@pytest.fixture
def multiple_companies_context(monkeypatch):
    """Context for a user with multiple companies."""
    user = {"id": 1, "email": "user@example.com", "is_super_admin": False}
    membership = {
        "company_id": 10,
        "is_admin": False,
        "can_access_shop": True,
        "can_access_cart": True,
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
            "available_companies": [
                {"company_id": 10, "company_name": "Company One"},
                {"company_id": 20, "company_name": "Company Two"},
                {"company_id": 30, "company_name": "Company Three"},
            ],
            "active_company": {"company_id": 10, "company_name": "Company One"},
            "active_company_id": membership["company_id"],
            "active_membership": membership,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)

    yield


@pytest.fixture
def no_companies_context(monkeypatch):
    """Context for a user with no companies."""
    user = {"id": 1, "email": "user@example.com", "is_super_admin": False}

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
            "active_company_id": None,
            "active_membership": None,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)

    yield


def test_company_switcher_hidden_with_single_company(single_company_context):
    """Company switcher should be hidden when user has only one company."""
    # Force complete template reload by recreating the Jinja2Templates object
    from starlette.templating import Jinja2Templates
    from pathlib import Path
    template_path = Path(__file__).resolve().parent.parent / "app" / "templates"
    main_module.templates = Jinja2Templates(directory=str(template_path))
    
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    
    # Company switcher should not be visible
    assert 'class="company-switcher"' not in html
    assert 'id="company-switcher"' not in html
    assert '/switch-company' not in html


def test_company_switcher_visible_with_multiple_companies(multiple_companies_context):
    """Company switcher should be visible when user has multiple companies."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    
    # Company switcher should be visible
    assert 'class="company-switcher"' in html
    assert 'id="company-switcher"' in html
    assert '/switch-company' in html
    
    # All companies should appear in the dropdown
    assert 'Company One' in html
    assert 'Company Two' in html
    assert 'Company Three' in html


def test_company_switcher_hidden_with_no_companies(no_companies_context):
    """Company switcher should be hidden when user has no companies."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    
    # Company switcher should not be visible
    assert 'class="company-switcher"' not in html
    assert 'id="company-switcher"' not in html
