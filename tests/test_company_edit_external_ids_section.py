"""Regression tests for the company External IDs section."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/company_edit.html")


def _section(template: str, key: str) -> str:
    start = template.index(f'data-company-edit-section="{key}"')
    end = template.index("</details>", start)
    return template[start:end]


def test_external_ids_are_grouped_in_their_own_section():
    template = TEMPLATE.read_text()
    section = _section(template, "external-ids")
    general_section = _section(template, "general")

    assert '<h2 class="card__title">External IDs</h2>' in section
    for field_name in (
        "tacticalClientId",
        "xeroId",
        "huduId",
        "huntressOrganizationId",
        "huntressSatAccountId",
    ):
        assert template.count(f'name="{field_name}"') == 1
        assert f'name="{field_name}"' in section
        assert f'name="{field_name}"' not in general_section


def test_external_ids_submit_with_company_settings_form():
    section = _section(TEMPLATE.read_text(), "external-ids")

    for field_name in (
        "tacticalClientId",
        "xeroId",
        "huduId",
        "huntressOrganizationId",
        "huntressSatAccountId",
    ):
        assert f'name="{field_name}" form="company-settings-form"' in section
    assert 'form="company-settings-form">Save company settings</button>' in section
