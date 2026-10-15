from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.datastructures import FormData

from app import main
from app.features.companies import handlers


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies/1/tray") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope, _dummy_receive)


def _stub_ticket_questions(monkeypatch, questions=None, conditions=None):
    import app.repositories.tray_ticket_questions as tq_repo

    monkeypatch.setattr(tq_repo, "list_questions", AsyncMock(return_value=list(questions or [])))
    monkeypatch.setattr(
        tq_repo, "list_conditions_for_questions", AsyncMock(return_value=list(conditions or []))
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_company_tray_settings_snippet_uses_https_when_request_is_http(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        companies_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "ACME"})
    )
    monkeypatch.setattr(tray_repo, "list_install_tokens", AsyncMock(return_value=[]))
    _stub_ticket_questions(monkeypatch)
    monkeypatch.setattr(main.settings, "portal_url", None)

    extra = await handlers._company_tray_settings_context(1, _make_request())

    assert extra["portal_url"] == "https://testserver"


@pytest.mark.anyio
async def test_company_tray_settings_snippet_prefers_configured_portal_url(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        companies_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "ACME"})
    )
    monkeypatch.setattr(tray_repo, "list_install_tokens", AsyncMock(return_value=[]))
    _stub_ticket_questions(monkeypatch)
    monkeypatch.setattr(
        main.settings, "portal_url", AnyHttpUrl("https://portal.example.com")
    )

    extra = await handlers._company_tray_settings_context(1, _make_request())

    assert extra["portal_url"] == "https://portal.example.com"


@pytest.mark.anyio
async def test_company_tray_settings_attaches_conditions_and_global_parents(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo
    import app.repositories.tray_ticket_questions as tq_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        companies_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "ACME"})
    )
    monkeypatch.setattr(tray_repo, "list_install_tokens", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.settings, "portal_url", None)

    company_question = {"id": 7, "scope": "company", "company_id": 1, "label": "Room"}
    global_question = {"id": 3, "scope": "global", "company_id": None, "label": "Site"}

    async def fake_list_questions(*, scope=None, company_id=None, active_only=False):
        return [dict(company_question)] if scope == "company" else [dict(global_question)]

    monkeypatch.setattr(tq_repo, "list_questions", fake_list_questions)
    monkeypatch.setattr(
        tq_repo,
        "list_conditions_for_questions",
        AsyncMock(
            return_value=[
                {"question_id": 7, "parent_question_id": 3, "operator": "equals", "expected_value": "Sydney"}
            ]
        ),
    )

    extra = await handlers._company_tray_settings_context(1, _make_request())

    assert extra["global_questions"] == [global_question]
    assert extra["company_questions"][0]["conditions"] == [
        {"question_id": 7, "parent_question_id": 3, "operator": "equals", "expected_value": "Sydney"}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
async def test_company_tray_settings_saves_setup_ticket_preference(monkeypatch, enabled):
    from app.repositories import companies
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(main, "_feature_pack_available", lambda name: False)
    monkeypatch.setattr(companies, "get_company_by_id", AsyncMock(return_value={"id": 1}))
    update = AsyncMock()
    monkeypatch.setattr(companies, "update_company", update)
    request = _make_request()
    monkeypatch.setattr(request, "form", AsyncMock(return_value=FormData(
        {"tray_setup_tickets_enabled": "1"} if enabled else {}
    )))
    response = await handlers.admin_company_tray_settings_save(1, request)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/companies/1/edit#ce-tray"
    assert update.call_args.args == (1,)
    assert update.call_args.kwargs["tray_setup_tickets_enabled"] == int(enabled)


def test_company_api_setup_ticket_preference():
    from app.schemas.companies import CompanyCreate, CompanyUpdate, CompanyResponse
    assert CompanyCreate(name="ACME").tray_setup_tickets_enabled is True
    assert CompanyResponse(id=1, name="ACME", tray_setup_tickets_enabled=0).tray_setup_tickets_enabled is False
    assert CompanyUpdate(tray_setup_tickets_enabled=False).model_dump(exclude_unset=True) == {
        "tray_setup_tickets_enabled": False
    }


@pytest.mark.anyio
async def test_old_tray_page_redirects_to_company_edit(monkeypatch):
    from app.repositories import companies

    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(companies, "get_company_by_id", AsyncMock(return_value={"id": 1}))
    response = await handlers.admin_company_tray_settings_page(1, _make_request(), new_token="abc+/=")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/companies/1/edit?new_token=abc%2B%2F%3D#ce-tray"


@pytest.mark.anyio
async def test_old_tray_page_preserves_authentication_and_missing_company(monkeypatch):
    from fastapi import HTTPException
    from starlette.responses import RedirectResponse
    from app.repositories import companies

    login = RedirectResponse("/login", status_code=303)
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=(None, login)))
    assert await handlers.admin_company_tray_settings_page(1, _make_request()) is login
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(companies, "get_company_by_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await handlers.admin_company_tray_settings_page(1, _make_request())
    assert exc.value.status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize("rmm_available", [False, True])
async def test_company_edit_loads_inline_tray_controls(monkeypatch, rmm_available):
    from html.parser import HTMLParser
    from jinja2 import ChoiceLoader, DictLoader
    from app.core.database import db
    from app.features.companies import business_hours_handlers
    from app.repositories import companies, tray, tray_ticket_questions

    company = {"id": 1, "name": "ACME", "email_domains": [], "tray_chat_enabled": 0}
    monkeypatch.setattr(db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(companies, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(handlers, "_get_company_management_scope", AsyncMock(return_value=(True, [company], {})))
    monkeypatch.setattr(main, "_get_m365_admin_credentials", AsyncMock(return_value=("", "", "")))
    monkeypatch.setattr(business_hours_handlers, "company_edit_context", AsyncMock(return_value={}))
    monkeypatch.setattr(tray, "list_install_tokens", AsyncMock(return_value=[{
        "id": 9, "label": "Fleet token", "token_prefix": "prefix", "use_count": 2,
    }]))
    company_question = {
        "id": 7, "scope": "company", "company_id": 1, "label": "Room", "field_type": "text",
        "options": [], "is_active": True, "sort_order": 0,
    }
    list_questions = AsyncMock(side_effect=[[company_question], []])
    monkeypatch.setattr(tray_ticket_questions, "list_questions", list_questions)
    monkeypatch.setattr(tray_ticket_questions, "list_conditions_for_questions", AsyncMock(return_value=[]))
    request = _make_request("/admin/companies/1/edit")
    request.scope["query_string"] = b"new_token=one-time-token"
    captured = {}

    async def render(template, request, user, *, extra):
        assert template == "admin/company_edit.html"
        env = main.templates.env.overlay(loader=ChoiceLoader([
            DictLoader({"base.html": "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"}),
            main.templates.env.loader,
        ]))
        html = env.get_template(template).render(
            **extra, request=request, user=user, module_enabled={},
            feature_pack_available=lambda name: name == "rmm" and rmm_available,
            static_url=lambda path: path, csrf_token="test-csrf",
        )
        captured["html"] = html
        return HTMLResponse(html)

    monkeypatch.setattr(main, "_render_template", render)
    response = await handlers._render_company_edit_page(request, {"id": 1}, company_id=1)
    assert response.status_code == 200
    list_questions.assert_any_await(scope="company", company_id=1, active_only=False)
    html = captured["html"]
    assert "Fleet token" in html
    assert "Room" in html
    assert "one-time-token" in html
    assert "https://testserver" in html
    assert "Save tray settings" in html
    assert "/static/js/tray_ticket_questions_admin.js" in html
    assert "Manage tray settings and tokens" not in html

    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.action = None
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                assert self.action is None, "Forms must not be nested"
                self.action = attrs.get("action", "")
            if tag == "input" and self.action == "/admin/companies/1/tray":
                self.fields[attrs.get("name")] = attrs

        def handle_endtag(self, tag):
            if tag == "form":
                self.action = None

    forms = Forms()
    forms.feed(html)
    assert "checked" not in forms.fields["tray_chat_enabled"]
    for name in ("customer_chat_enabled", "tray_notifications_enabled", "tray_setup_tickets_enabled"):
        assert "checked" in forms.fields[name]
    assert ("rmm_agent_auto_deploy" in forms.fields) is rmm_available
    assert forms.fields["_csrf"]["value"] == "test-csrf"
