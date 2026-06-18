from app.repositories import shop as shop_repo


def test_normalise_product_summary_defaults_missing_stock_feed_flags() -> None:
    row = {
        "id": 1,
        "name": "Widget",
        "sku": "W-1",
        "vendor_sku": None,
        "image_url": None,
        "price": "10.50",
        "vip_price": None,
        "buy_price": None,
        "stock": 3,
        "archived": 0,
        "category_id": None,
        "category_name": None,
        "duplicate_sku_import": None,
        "duplicate_sku_count": None,
    }

    product = shop_repo._normalise_product_summary(row)

    assert product["duplicate_sku_import"] is False
    assert product["duplicate_sku_count"] == 0
