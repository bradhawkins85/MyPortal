"""Regression tests for the company OneDrive export setting layout."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/company_edit.html")


def test_onedrive_export_setting_is_inside_microsoft_365_section():
    template = TEMPLATE.read_text()
    m365_section_start = template.index(
        '<details class="card card--panel card-collapsible admin-grid__full" '
        'data-m365-credentials-panel>'
    )
    m365_section_end = template.index("</details>", m365_section_start)
    onedrive_setting = template.index(
        'for="edit-company-onedrive-export-site"', m365_section_start
    )

    assert m365_section_start < onedrive_setting < m365_section_end
    assert template.count('for="edit-company-onedrive-export-site"') == 1


def test_onedrive_export_setting_submits_with_company_settings_form():
    template = TEMPLATE.read_text()

    assert '<form id="company-settings-form"' in template
    assert 'data-onedrive-export-sites-select\n                  form="company-settings-form"' in template
    assert 'form="company-settings-form">Save company settings</button>' in template
