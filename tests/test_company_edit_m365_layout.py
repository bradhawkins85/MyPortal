"""Regression tests for the Microsoft 365 company settings layout."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/company_edit.html")


def test_offboarding_email_forwarding_is_inside_microsoft_365_section():
    template = TEMPLATE.read_text()
    m365_section_start = template.index("data-m365-credentials-panel")
    forwarding_setting = template.index(
        'id="edit-company-offboarding-email-forwarding"'
    )
    credentials_form = template.index(
        'action="/admin/companies/{{ company.id }}/m365-credentials"'
    )

    assert m365_section_start < forwarding_setting < credentials_form
    assert template.count('id="edit-company-offboarding-email-forwarding"') == 1
    assert 'name="offboardingEmailForwardingEnabled"\n                form="company-settings-form"' in template


def test_back_to_companies_only_appears_in_page_header():
    template = TEMPLATE.read_text()

    assert template.count('"label": "Back to companies"') == 1
    assert '>Back to companies</a>' not in template
