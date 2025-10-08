from app.services import template_variables


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
