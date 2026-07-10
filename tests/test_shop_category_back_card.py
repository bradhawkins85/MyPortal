from pathlib import Path


def test_category_back_card_is_first_product_grid_item():
    template = Path("app/templates/shop/index.html").read_text()

    flag_position = template.index("showing_product_category")
    grid_position = template.index('<div class="shop-card-grid">')
    back_card_position = template.index("Main shop")
    product_loop_position = template.index("{% for product in products %}")

    assert flag_position < grid_position
    assert grid_position < back_card_position < product_loop_position
    assert "href=\"{{ request.url_for('shop_page') }}{{ shop_query() }}\"" in template
    assert "Return to the main shop page" in template
