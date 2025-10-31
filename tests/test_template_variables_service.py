from app.services import template_variables
from app.services import message_templates as message_templates_service


def test_build_template_replacements_and_apply():
    context = template_variables.TemplateContext(
        user=template_variables.TemplateContextUser(
            id=42,
            email="user@example.com",
            first_name="Ada",
            last_name="Lovelace",
        ),
        company=template_variables.TemplateContextCompany(
            id=7,
            name="ACME Corp",
            syncro_customer_id="SYNC123",
        ),
        portal=template_variables.TemplateContextPortal(
            base_url="https://portal.example.com",
            login_url="https://portal.example.com/login",
        ),
    )

    replacements = template_variables.build_template_replacement_map(context)

    assert replacements["{{user.email}}"] == "user@example.com"
    assert replacements["{{user.emailUrlEncoded}}"] == "user%40example.com"
    assert replacements["{{company.id}}"] == "7"
    assert replacements["{{portal.loginUrlUrlEncoded}}"] == "https%3A%2F%2Fportal.example.com%2Flogin"

    value = template_variables.apply_template_variables(
        "Hello {{user.fullName}} from {{company.name}}",
        replacements,
    )
    assert value == "Hello Ada Lovelace from ACME Corp"


def test_apply_template_variables_handles_missing_values():
    replacements = template_variables.build_template_replacement_map(
        template_variables.TemplateContext()
    )
    result = template_variables.apply_template_variables(
        "{{user.email}}-{{company.id}}-{{portal.baseUrl}}",
        replacements,
    )
    assert result == "--"


def test_template_replacements_include_message_templates(monkeypatch):
    template_records = [
        {
            "id": 1,
            "slug": "greeting",
            "name": "Greeting",
            "description": None,
            "content_type": "text/plain",
            "content": "Hello {{user.firstName}}",
            "created_at": None,
            "updated_at": None,
        }
    ]

    monkeypatch.setattr(
        message_templates_service,
        "iter_templates",
        lambda: template_records,
    )

    context = template_variables.TemplateContext(
        user=template_variables.TemplateContextUser(first_name="Sam"),
    )

    replacements = template_variables.build_template_replacement_map(context)

    assert replacements["{{TEMPLATE_GREETING}}"] == "Hello Sam"
    assert replacements["{{template.greeting}}"] == "Hello Sam"
