from __future__ import annotations

from pathlib import Path

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


def test_signature_templates_compile_and_expose_designer_copy():
    environment = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))

    environment.get_template("m365/signatures.html")
    environment.get_template("m365/signatures_form.html")

    source = (ROOT / "app" / "templates" / "m365" / "signatures_form.html").read_text(
        encoding="utf-8"
    )
    assert "Supports formatting, links, images, tables, and template variables." in source
    assert "Plain-text signature" in source
    assert "Preview staff member" in source


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
