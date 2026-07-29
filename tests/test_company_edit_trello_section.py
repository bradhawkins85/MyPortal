"""Regression tests for collapsible company edit sections."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/company_edit.html")
SCRIPT = Path("app/static/js/company_edit_sections.js")


def _section(template: str, key: str) -> str:
    start = template.index(f'data-company-edit-section="{key}"')
    end = template.index("</details>", start)
    return template[start:end]


def test_trello_settings_are_grouped_in_their_own_collapsible_section():
    template = TEMPLATE.read_text()
    section = _section(template, "trello")

    assert '<h2 class="card__title">Trello</h2>' in section
    for field_name in ("trelloBoardId", "trelloApiKey", "trelloToken"):
        assert template.count(f'name="{field_name}"') == 1
        assert f'name="{field_name}"' in section
        assert f'name="{field_name}" form="company-settings-form"' in section
    assert 'id="trello-register-webhook-btn"' in section


def test_general_company_settings_are_collapsible():
    template = TEMPLATE.read_text()
    section = _section(template, "general")

    assert section.startswith('data-company-edit-section="general" open>')
    assert 'id="company-settings-form"' in section


def test_section_state_is_shared_across_company_pages():
    script = SCRIPT.read_text()

    assert "myportal:company-edit:sections" in script
    assert "data-company-id" not in script
    assert "window.localStorage.getItem(STORAGE_KEY)" in script
    assert "window.localStorage.setItem(STORAGE_KEY" in script
    assert ".company-edit-page > .admin-grid > details.card-collapsible" in script
