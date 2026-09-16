from __future__ import annotations

from pathlib import Path
from datetime import date

import pytest
from jinja2 import Environment, FileSystemLoader

from app.security.menu_permissions import catalogue_for_api
from app.services import m365_signatures


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_generate_initial_text_from_html():
    html = """
    <p><strong>Ada Lovelace</strong></p>
    <p>Platform Engineer</p>
    <table><tr><td>Phone</td><td>+61 400 000 000</td></tr></table>
    """

    text = m365_signatures.generate_initial_text(html)

    assert "Ada Lovelace" in text
    assert "Platform Engineer" in text
    assert "Phone" in text
    assert "<strong>" not in text


@pytest.mark.anyio
async def test_render_preview_resolves_context_and_flags_missing(monkeypatch):
    async def fake_context(company_id: int, staff_id: int):
        assert (company_id, staff_id) == (7, 21)
        return {
            "staff": {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "custom": {"pronouns": "she/her"},
            },
            "user": {"fullName": "Ada Lovelace"},
            "company": {"name": "Analytical Engines", "variables": {"support_phone": "1300 000 000"}},
        }

    monkeypatch.setattr(m365_signatures, "build_preview_context", fake_context)

    preview = await m365_signatures.render_preview(
        7,
        html_content="""
        <p>{{staff.first_name}} {{staff.last_name}}</p>
        <p>{{staff.custom.pronouns}}</p>
        <p>{{company.variables.support_phone}}</p>
        <p>{{missing.value}}</p>
        """,
        text_content="Name: {{user.fullName}}\nPhone: {{company.variables.support_phone}}",
        staff_id=21,
    )

    assert "Ada Lovelace" in preview["html"]
    assert "she/her" in preview["html"]
    assert "1300 000 000" in preview["text"]
    assert preview["missing_tokens"] == ["missing.value"]


@pytest.mark.anyio
async def test_variable_suggestions_include_company_variables(monkeypatch):
    async def fake_list_for_company(company_id: int):
        assert company_id == 7
        return [{"name": "support_phone", "value": "1300 000 000"}]

    monkeypatch.setattr(
        m365_signatures.company_variables_repo,
        "list_for_company",
        fake_list_for_company,
    )

    suggestions = await m365_signatures.list_variable_suggestions(7)

    assert "{{company.variables.support_phone}}" in suggestions
    assert "{{custom.support_phone}}" in suggestions
    assert suggestions.count("{{company.variables.support_phone}}") == 1


@pytest.mark.anyio
async def test_clone_template_truncates_slug_before_copy_suffix(monkeypatch):
    source_slug = "x" * 120

    async def fake_get_template(company_id: int, template_id: int):
        return {
            "id": template_id,
            "slug": source_slug,
            "name": "Primary",
            "description": None,
            "html_content": "<p>Hello</p>",
            "text_content": "Hello",
        }

    async def fake_get_template_by_slug(company_id: int, slug: str):
        assert len(slug) <= 120
        return None

    async def fake_create_template(**kwargs):
        return kwargs

    monkeypatch.setattr(m365_signatures, "get_template", fake_get_template)
    monkeypatch.setattr(
        m365_signatures.signatures_repo,
        "get_template_by_slug",
        fake_get_template_by_slug,
    )
    monkeypatch.setattr(
        m365_signatures.signatures_repo,
        "create_template",
        fake_create_template,
    )

    cloned = await m365_signatures.clone_template(7, 11, user_id=5)

    assert cloned is not None
    assert cloned["slug"].endswith("-copy")
    assert len(cloned["slug"]) <= 120


def test_signature_templates_compile_and_expose_designer_copy():
    environment = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))

    environment.get_template("m365/signatures.html")
    environment.get_template("m365/signatures_form.html")

    source = (ROOT / "app" / "templates" / "m365" / "signatures_form.html").read_text(
        encoding="utf-8"
    )
    assert "pasted signature content" in source
    assert "Plain-text signature" in source
    assert "Preview staff member" in source
    assert "Activation start date" in source
    assert "purify.min.js" in source


def test_pick_primary_template_prefers_priority_then_default_then_schedule():
    primary = m365_signatures.pick_primary_template(
        [
            {
                "id": 1,
                "status": "published",
                "priority": 5,
                "is_default": True,
                "schedule_start_on": None,
                "schedule_end_on": None,
            },
            {
                "id": 2,
                "status": "published",
                "priority": 8,
                "is_default": False,
                "schedule_start_on": date(2026, 9, 1),
                "schedule_end_on": date(2026, 9, 30),
            },
        ],
        on_date=date(2026, 9, 16),
    )

    assert primary is not None
    assert primary["id"] == 2


def test_pick_primary_template_uses_default_for_equal_priority():
    primary = m365_signatures.pick_primary_template(
        [
            {
                "id": 4,
                "status": "published",
                "priority": 3,
                "is_default": False,
                "schedule_start_on": date(2026, 9, 1),
                "schedule_end_on": date(2026, 9, 30),
            },
            {
                "id": 5,
                "status": "published",
                "priority": 3,
                "is_default": True,
                "schedule_start_on": None,
                "schedule_end_on": None,
            },
        ],
        on_date=date(2026, 9, 16),
    )

    assert primary is not None
    assert primary["id"] == 5


def test_is_template_active_respects_inclusive_schedule_bounds():
    template = {
        "status": "published",
        "schedule_start_on": date(2026, 9, 1),
        "schedule_end_on": date(2026, 9, 30),
    }

    assert m365_signatures.is_template_active(template, on_date=date(2026, 9, 1)) is True
    assert m365_signatures.is_template_active(template, on_date=date(2026, 9, 30)) is True
    assert m365_signatures.is_template_active(template, on_date=date(2026, 10, 1)) is False


def test_pick_primary_template_uses_earliest_start_date_for_equal_priority():
    primary = m365_signatures.pick_primary_template(
        [
            {
                "id": 7,
                "status": "published",
                "priority": 4,
                "is_default": False,
                "schedule_start_on": date(2026, 9, 10),
                "schedule_end_on": date(2026, 9, 30),
            },
            {
                "id": 8,
                "status": "published",
                "priority": 4,
                "is_default": False,
                "schedule_start_on": date(2026, 9, 1),
                "schedule_end_on": date(2026, 9, 30),
            },
        ],
        on_date=date(2026, 9, 16),
    )

    assert primary is not None
    assert primary["id"] == 8


def test_signature_sidebar_requires_explicit_permission():
    source = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert source.count("{% if can_access_m365_signatures %}") == 2
    assert "or can_access_m365_signatures or" in source


def test_signature_permission_is_available_to_roles():
    permission = next(
        item for item in catalogue_for_api() if item["key"] == "menu.m365.signatures"
    )

    assert permission["admin_only"] is False
    assert permission["levels"] == ["none", "read", "write"]
