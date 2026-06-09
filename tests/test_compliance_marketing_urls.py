"""Tests for Essential 8 compliance help links."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.features.compliance import routes as compliance_routes
from app.features.marketing import routes as marketing_routes


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str) -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


class MockFormRequest:
    def __init__(self, form_data: dict[str, str]) -> None:
        self._form_data = form_data

    async def form(self) -> dict[str, str]:
        return self._form_data


def test_slugify_essential8_element():
    assert (
        compliance_routes._slugify_essential8_element("Patch Applications & Software")
        == "patch-applications-software"
    )


def test_build_essential8_help_url_appends_element_query():
    assert (
        compliance_routes._build_essential8_help_url(
            "/marketing/essential8",
            "application-control",
        )
        == "/marketing/essential8?element=application-control"
    )


def test_build_essential8_help_url_preserves_existing_query():
    assert (
        compliance_routes._build_essential8_help_url(
            "/marketing/essential8?utm=campaign",
            "application-control",
        )
        == "/marketing/essential8?utm=campaign&element=application-control"
    )


def test_build_essential8_help_url_replaces_placeholder():
    assert (
        compliance_routes._build_essential8_help_url(
            "https://example.com/essential8/{element}",
            "application-control",
        )
        == "https://example.com/essential8/application-control"
    )


@pytest.mark.anyio("asyncio")
async def test_compliance_page_no_longer_sets_per_control_help(monkeypatch):
    request = _make_request("/compliance")
    captured: dict[str, object] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template_name"] = template_name
        captured["extra"] = extra
        return extra

    monkeypatch.setattr(
        compliance_routes,
        "_load_compliance_context",
        AsyncMock(return_value=({"id": 1, "is_super_admin": False}, {}, {"id": 2, "name": "Acme"}, 2, None)),
    )
    monkeypatch.setattr(
        compliance_routes.essential8_repo,
        "list_company_compliance",
        AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "control_id": 10,
                    "status": "in_progress",
                    "control": {"name": "Application Control", "control_order": 1},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        compliance_routes.essential8_repo,
        "get_per_maturity_statuses_for_company",
        AsyncMock(return_value={10: {"ml1": "in_progress", "ml2": "not_started", "ml3": "not_started"}}),
    )
    monkeypatch.setattr(
        compliance_routes.essential8_repo,
        "get_company_compliance_summary",
        AsyncMock(
            return_value={
                "compliance_percentage": 0,
                "compliant": 0,
                "total_controls": 8,
                "in_progress": 1,
                "not_started": 7,
                "average_maturity_level": 0,
            }
        ),
    )
    monkeypatch.setattr(
        compliance_routes,
        "_main",
        lambda: SimpleNamespace(_render_template=fake_render_template),
    )

    await compliance_routes.compliance_page(request)

    record = captured["extra"]["compliance_records"][0]
    assert record["show_compliance_help"] is False
    assert record["compliance_help_url"] == ""


@pytest.mark.anyio("asyncio")
async def test_control_requirements_page_sets_help_per_requirement(monkeypatch):
    request = _make_request("/compliance/control/1")
    captured: dict[str, object] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return extra

    monkeypatch.setattr(
        compliance_routes,
        "_load_compliance_context",
        AsyncMock(return_value=({"id": 1, "is_super_admin": False}, {}, {"id": 2, "name": "Acme"}, 2, None)),
    )
    monkeypatch.setattr(
        compliance_routes.essential8_repo,
        "get_control_with_requirements",
        AsyncMock(
            return_value={
                "control": {"id": 1, "name": "Application Control", "description": "Desc"},
                "requirements_ml1": [
                    {"id": 101, "requirement_order": 1, "description": "Needs help", "maturity_level": "ml1"},
                    {"id": 102, "requirement_order": 2, "description": "Done", "maturity_level": "ml1"},
                ],
                "requirements_ml2": [],
                "requirements_ml3": [],
                "company_compliance": {"status": "in_progress"},
                "requirement_compliance": [
                    {"requirement_id": 101, "status": "in_progress"},
                    {"requirement_id": 102, "status": "compliant"},
                ],
            }
        ),
    )
    monkeypatch.setattr(
        compliance_routes.essential8_repo,
        "list_requirement_marketing_page_links",
        AsyncMock(
            return_value=[
                {
                    "requirement_id": 101,
                    "marketing_page_id": 9,
                    "marketing_page_slug": "essential8-help",
                    "marketing_page_title": "Essential 8 Help",
                    "marketing_page_is_published": True,
                },
                {
                    "requirement_id": 102,
                    "marketing_page_id": 9,
                    "marketing_page_slug": "essential8-help",
                    "marketing_page_title": "Essential 8 Help",
                    "marketing_page_is_published": True,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        compliance_routes.essential8_repo,
        "get_per_maturity_statuses_for_company",
        AsyncMock(return_value={1: {"ml1": "in_progress", "ml2": "not_started", "ml3": "not_started"}}),
    )
    monkeypatch.setattr(
        compliance_routes,
        "_main",
        lambda: SimpleNamespace(_render_template=fake_render_template),
    )

    await compliance_routes.compliance_control_requirements_page(request, control_id=1)

    requirements = captured["extra"]["requirements_ml1"]
    assert requirements[0]["show_compliance_help"] is True
    assert requirements[0]["compliance_help_url"] == "/marketing/essential8-help"
    assert requirements[1]["show_compliance_help"] is False
    assert requirements[1]["compliance_help_url"] == ""


@pytest.mark.anyio("asyncio")
async def test_marketing_help_links_page_shows_requirement_mappings_for_super_admin(monkeypatch):
    request = _make_request("/admin/marketing/essential8-help-links")
    captured: dict[str, object] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template_name"] = template_name
        captured["extra"] = extra
        return extra

    monkeypatch.setattr(
        marketing_routes,
        "_require_marketing_access",
        AsyncMock(return_value=({"id": 1, "is_super_admin": True}, None)),
    )
    monkeypatch.setattr(marketing_routes.marketing_repo, "list_pages", AsyncMock(return_value=[{"id": 7, "title": "Page", "is_published": True}]))
    monkeypatch.setattr(marketing_routes.marketing_repo, "list_leads", AsyncMock(return_value=[]))
    monkeypatch.setattr(marketing_routes.essential8_repo, "list_essential8_controls", AsyncMock(return_value=[{"id": 1, "control_order": 1, "name": "Application Control", "description": "Desc"}]))
    monkeypatch.setattr(
        marketing_routes.essential8_repo,
        "list_essential8_requirements",
        AsyncMock(return_value=[{"id": 101, "control_id": 1, "maturity_level": "ml1", "requirement_order": 1, "description": "Req"}]),
    )
    monkeypatch.setattr(
        marketing_routes.essential8_repo,
        "list_requirement_marketing_page_links",
        AsyncMock(
            return_value=[
                {
                    "requirement_id": 101,
                    "marketing_page_id": 7,
                    "marketing_page_slug": "page",
                    "marketing_page_title": "Page",
                    "marketing_page_is_published": True,
                }
            ]
        ),
    )
    monkeypatch.setattr(marketing_routes, "_main", lambda: SimpleNamespace(_render_template=fake_render_template))

    await marketing_routes.admin_marketing_essential8_help_links(request)

    assert captured["template_name"] == "admin/marketing_essential8_help_links.html"
    controls = captured["extra"]["essential8_help_controls"]
    assert len(controls) == 1
    assert controls[0]["requirements"][0]["selected_marketing_page_id"] == 7


@pytest.mark.anyio("asyncio")
async def test_marketing_update_help_links_requires_super_admin(monkeypatch):
    monkeypatch.setattr(
        marketing_routes,
        "_require_marketing_access",
        AsyncMock(return_value=({"id": 2, "is_super_admin": False}, None)),
    )

    with pytest.raises(HTTPException) as excinfo:
        await marketing_routes.admin_marketing_update_essential8_help_links(MockFormRequest({}))

    assert excinfo.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_marketing_update_help_links_replaces_mappings(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_replace(mappings):
        captured["mappings"] = mappings

    monkeypatch.setattr(
        marketing_routes,
        "_require_marketing_access",
        AsyncMock(return_value=({"id": 1, "is_super_admin": True}, None)),
    )
    monkeypatch.setattr(
        marketing_routes.marketing_repo,
        "list_pages",
        AsyncMock(return_value=[{"id": 7, "title": "Page", "is_published": True}]),
    )
    monkeypatch.setattr(
        marketing_routes.essential8_repo,
        "list_essential8_requirements",
        AsyncMock(
            return_value=[
                {"id": 101, "control_id": 1},
                {"id": 102, "control_id": 1},
            ]
        ),
    )
    monkeypatch.setattr(
        marketing_routes.essential8_repo,
        "replace_requirement_marketing_page_links",
        fake_replace,
    )

    response = await marketing_routes.admin_marketing_update_essential8_help_links(
        MockFormRequest({"requirement_101": "7", "requirement_102": ""})
    )

    assert response.status_code == 303
    assert captured["mappings"] == {101: 7, 102: None}
