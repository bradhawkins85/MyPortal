"""Regression tests for the company edit settings workspace sections."""

import re
from pathlib import Path


TEMPLATE = Path("app/templates/admin/company_edit.html")
SCRIPT = Path("app/static/js/company_edit_sections.js")


def _section(template: str, key: str) -> str:
    start = template.index(f'data-company-edit-section="{key}"')
    end = template.find('<section class="card card--panel ce-section"', start)
    return template[start:end if end != -1 else len(template)]


def test_trello_settings_are_grouped_in_their_own_collapsible_section():
    template = TEMPLATE.read_text()
    section = _section(template, "trello")

    assert '<h2 class="card__title" id="ce-trello-title">Trello</h2>' in section
    for field_name in ("trelloBoardId", "trelloApiKey", "trelloToken"):
        assert template.count(f'name="{field_name}"') == 1
        assert f'name="{field_name}"' in section
        assert f'name="{field_name}" form="company-settings-form"' in section
    assert 'id="trello-register-webhook-btn"' in section


def test_general_company_settings_are_the_profile_section():
    template = TEMPLATE.read_text()
    section = _section(template, "general")

    assert 'id="ce-general-title">Profile</h2>' in section
    assert 'id="company-settings-form"' in section
    # Sections are always rendered; the workspace script decides which one shows.
    assert "<details" not in template[: template.index('data-company-edit-section="general"')]


def test_every_section_has_a_navigation_link():
    template = TEMPLATE.read_text()
    sections = set(re.findall(r'<section class="card card--panel ce-section"[^>]*data-company-edit-section="([a-z0-9-]+)"', template))
    nav_keys = set(re.findall(r'\("([a-z0-9-]+)", "[a-z0-9-]+", "[^"]+",', template))

    assert sections
    assert sections == nav_keys


def test_active_section_is_remembered_across_company_pages():
    script = SCRIPT.read_text()

    assert "myportal:company-edit:active-section" in script
    assert "data-company-id" not in script
    assert "window.localStorage.getItem(STORAGE_KEY)" in script
    assert "window.localStorage.setItem(STORAGE_KEY" in script
    assert "ce:section-shown" in script
