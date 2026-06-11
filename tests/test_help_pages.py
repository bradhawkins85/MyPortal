from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.features.help import routes as help_routes
from app.main import app, scheduler_service


def _sample_sections():
    return [
        {
            "name": "Getting Started",
            "slug": "getting-started",
            "articles": [
                {
                    "name": "Welcome",
                    "slug": "welcome",
                    "section": "Getting Started",
                    "section_slug": "getting-started",
                    "path": Path("/tmp/welcome.md"),
                },
                {
                    "name": "First Steps",
                    "slug": "first-steps",
                    "section": "Getting Started",
                    "section_slug": "getting-started",
                    "path": Path("/tmp/first-steps.md"),
                },
            ],
        },
        {
            "name": "Automation",
            "slug": "automation",
            "articles": [
                {
                    "name": "Rules Overview",
                    "slug": "rules-overview",
                    "section": "Automation",
                    "section_slug": "automation",
                    "path": Path("/tmp/rules-overview.md"),
                }
            ],
        },
    ]


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    class DummyCursor:
        async def execute(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

    class DummyConnection:
        def cursor(self, *args, **kwargs):
            return DummyCursor()

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[DummyConnection]:
        yield DummyConnection()

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

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "acquire", fake_acquire)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def patched_dependencies(monkeypatch):
    async def fake_require_user(request):
        return {"id": 1, "email": "user@example.com"}, None

    async def fake_require_menu_page_access(request, key, *, write=False, detail="Page access permission required"):
        assert key == "menu.help"
        return {"id": 1, "email": "user@example.com"}, None

    async def fake_build_base_context(request, user, *, extra=None):
        context = {
            "request": request,
            "app_name": "MyPortal",
            "current_year": 2026,
            "current_user": user,
            "available_companies": [],
            "active_company": None,
            "active_company_id": None,
            "active_membership": None,
            "csrf_token": "csrf-token",
            "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": 0},
            "notification_unread_count": 0,
            "plausible_config": {"enabled": False, "site_domain": "", "base_url": ""},
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_require_menu_page_access", fake_require_menu_page_access)
    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    main_module.templates.env.globals["plausible_config"] = {
        "enabled": False,
        "site_domain": "",
        "base_url": "",
    }

    yield


def test_help_index_uses_sidebar_navigation_layout(patched_dependencies, monkeypatch):
    monkeypatch.setattr(help_routes, "list_sections", lambda: _sample_sections())

    with TestClient(app) as client:
        response = client.get("/help")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert 'class="help__layout"' in response.text
    assert 'class="help__sidebar card card--panel"' in response.text
    assert 'class="help__content help__content--placeholder card card--panel"' in response.text
    assert "Select an article from the left menu." in response.text
    assert 'class="help__content-stack"' not in response.text
    assert 'class="help__nav-link"' in response.text
    assert "help__article-icon" not in response.text


def test_help_article_marks_active_nav_item_and_uses_rich_text_viewer(patched_dependencies, monkeypatch):
    with TestClient(app) as client:
        response = client.get("/help/getting-started/home")

    assert response.status_code == 200
    assert 'class="help__content card card--panel"' in response.text
    assert 'class="help__article-body rich-text-viewer"' in response.text
    assert "help__nav-item--active" in response.text
    assert "MyPortal Wiki" in response.text
