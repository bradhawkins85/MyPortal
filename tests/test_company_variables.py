from pathlib import Path

from app.features.tickets.admin_routes import _ticket_template_context
from app.services.message_templates import render_content


def test_company_variables_section_documents_token():
    template = Path("app/templates/admin/company_edit.html").read_text()
    start = template.index('data-company-edit-section="company-variables"')
    section = template[start:template.index('<section class="card card--panel ce-section"', start)]
    assert "company.variables.SUPPORT_PORTAL_URL" in section
    # Each variable shows its own copyable template token.
    assert "{% set token = '{{ company.variables.' ~ variable.name ~ ' }}' %}" in section
    assert 'data-ce-copy="{{ token }}"' in section
    assert "company.variables.VARIABLE_NAME" in template


def test_company_variable_is_available_to_canned_response_renderer():
    context = _ticket_template_context(
        {"company_name": "Acme"}, {"SUPPORT_PORTAL_URL": "https://acme.example/support"}
    )
    assert render_content("Visit {{ company.variables.SUPPORT_PORTAL_URL }}", context) == (
        "Visit https://acme.example/support"
    )
