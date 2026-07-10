from pathlib import Path


def test_main_shop_card_is_first_filtered_shop_grid_item():
    template = Path("app/templates/shop/index.html").read_text()

    flag_position = template.index("show_main_shop_card")
    grid_position = template.index('<div class="shop-card-grid">')
    back_card_position = template.index("Main shop")
    product_loop_position = template.index("{% for product in products %}")

    assert flag_position < grid_position
    assert grid_position < back_card_position < product_loop_position
    assert "href=\"{{ request.url_for('shop_page') }}\"" in template
    assert "Return to the main shop page" in template
    assert "{% if show_main_shop_card %}" in template


def test_main_shop_card_shows_for_category_packages_and_featured_pages():
    template = Path("app/templates/shop/index.html").read_text()

    assert "current_category is not none" in template
    assert "not showing_category_cards" in template
    handlers = Path("app/features/shop/handlers.py").read_text()

    assert '"current_category": "packages" if show_packages else "featured"' in handlers


def test_shop_template_has_no_bottom_pagination():
    template = Path("app/templates/shop/index.html").read_text()

    assert "table-pagination" not in template
    assert "Page {{ page }}" not in template
    assert "shop_query(current_category, page" not in template
    assert 'name="pageSize"' not in template
