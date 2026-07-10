from pathlib import Path


def test_shop_product_details_modal_has_super_admin_refresh_form() -> None:
    template = Path("app/templates/shop/index.html").read_text()

    assert "{% if is_shop_super_admin %}" in template
    assert "data-product-refresh-form" in template
    assert "data-product-refresh-button" in template
    assert "Refresh product details" in template
    assert "{% include \"partials/csrf.html\" %}" in template


def test_shop_product_details_refresh_form_uses_admin_refresh_endpoint() -> None:
    script = Path("app/static/js/shop.js").read_text()

    assert "detailsModal.querySelector('[data-product-refresh-form]')" in script
    assert "refreshForm.action = `/shop/admin/product/${id}/refresh-description`;" in script
    assert "refreshForm.hidden = false;" in script
    assert "refreshForm.hidden = true;" in script
