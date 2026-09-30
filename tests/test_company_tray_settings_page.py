from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse

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

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await handlers.admin_company_tray_settings_page(1, _make_request(), new_token="abc")

    assert captured["extra"]["portal_url"] == "https://testserver"


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

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await handlers.admin_company_tray_settings_page(1, _make_request(), new_token="abc")

    assert captured["extra"]["portal_url"] == "https://portal.example.com"


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

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await handlers.admin_company_tray_settings_page(1, _make_request())

    extra = captured["extra"]
    assert extra["global_questions"] == [global_question]
    assert extra["company_questions"][0]["conditions"] == [
        {"question_id": 7, "parent_question_id": 3, "operator": "equals", "expected_value": "Sydney"}
    ]
