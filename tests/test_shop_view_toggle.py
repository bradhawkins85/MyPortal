from pathlib import Path


def test_shop_page_supports_persistent_card_and_row_views() -> None:
    template = Path("app/templates/shop/index.html").read_text(encoding="utf-8")
    script = Path("app/static/js/shop.js").read_text(encoding="utf-8")
    stylesheet = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert 'data-shop-view="card"' in template
    assert 'data-shop-view="row"' in template
    assert '<div class="shop-card-grid">' in template
    assert "SHOP_VIEW_STORAGE_KEY" in script
    assert "bindShopViewToggle(container)" in script
    assert ".shop-card-grid--rows" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr)" in stylesheet
    assert "grid-template-columns: 9rem minmax(0, 1fr) minmax(14rem, auto)" in stylesheet
    assert "height: 9rem" in stylesheet


def test_subscription_shop_omits_stock_controls_and_redundant_main_tile() -> None:
    template = Path("app/templates/shop/index.html").read_text(encoding="utf-8")

    assert "{% if not show_subscriptions %}" in template
    assert 'shop-tile--special\" href=\"{{ request.url_for(\'shop_page\') }}?category=subscriptions' not in template
    assert "status-badge--success\">Available" not in template
