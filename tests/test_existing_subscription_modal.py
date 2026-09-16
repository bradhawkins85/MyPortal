from pathlib import Path


TEMPLATE = Path("app/templates/subscriptions/index.html").read_text(encoding="utf-8")


def test_super_admin_existing_subscription_action_is_on_portal_page():
    action_block = TEMPLATE[
        TEMPLATE.index("{% block header_actions %}") : TEMPLATE.index(
            "{% endblock %}", TEMPLATE.index("{% block header_actions %}")
        )
    ]

    assert "{% if is_super_admin %}" in action_block
    assert "data-open-existing-subscription" in action_block
    assert "Add existing subscription" in action_block


def test_existing_subscription_modal_has_all_api_fields():
    modal_start = TEMPLATE.index('id="existing-subscription-dialog"')
    modal = TEMPLATE[modal_start : TEMPLATE.index("</dialog>", modal_start)]

    for field in ("customerId", "productId", "startDate", "quantity", "autoRenew"):
        assert f'name="{field}"' in modal
    assert "creation_companies" in modal
    assert "creation_products" in modal


def test_existing_subscription_modal_calls_existing_api_with_csrf():
    assert "fetch('/api/v1/subscriptions'" in TEMPLATE
    assert "'X-CSRF-Token': csrfToken || ''" in TEMPLATE
    assert "credentials: 'include'" in TEMPLATE
    assert "showExistingErrors" in TEMPLATE
    assert "created successfully" in TEMPLATE
