from pathlib import Path

from app.features.tickets.admin_routes import _ticket_template_context
from app.services.message_templates import render_content


def test_company_variables_section_is_collapsed_and_documents_token():
    template = Path("app/templates/admin/company_edit.html").read_text()
    marker = 'data-company-edit-section="company-variables"'
    details = template[template.index("<details", template.index(marker) - 120):template.index(marker)]
    assert " open" not in details
    assert "company.variables.VARIABLE_NAME" in template


def test_company_variable_is_available_to_canned_response_renderer():
    context = _ticket_template_context(
        {"company_name": "Acme"}, {"SUPPORT_PORTAL_URL": "https://acme.example/support"}
    )
    assert render_content("Visit {{ company.variables.SUPPORT_PORTAL_URL }}", context) == (
        "Visit https://acme.example/support"
    )
