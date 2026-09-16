from pathlib import Path


def test_product_details_uses_manage_for_existing_subscription():
    script = Path("app/static/js/shop.js").read_text(encoding="utf-8")
    assert "if (product && product.active_subscription_id)" in script
    assert "manage.textContent = 'Manage'" in script
    assert "/subscriptions#subscription-" in script


def test_subscription_management_rows_are_linkable():
    template = Path("app/templates/subscriptions/index.html").read_text(encoding="utf-8")
    assert 'id="subscription-{{ subscription.id }}"' in template
