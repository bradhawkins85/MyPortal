from app.features.shop import handlers


def test_public_shop_product_payload_strips_internal_fields() -> None:
    product = {
        "id": 10,
        "name": "Router",
        "sku": "R-1",
        "description": "<p>desc</p>",
        "image_url": "https://example.invalid/router.png",
        "price": 25.0,
        "vip_price": 20.0,
        "stock": 3,
        "stock_nsw": 1,
        "stock_qld": 1,
        "stock_vic": 1,
        "stock_sa": 0,
        "category_id": 4,
        "category_name": "Hardware",
        "features": [{"id": 1, "name": "Fast"}],
        "cross_sell_products": [{"id": 7}],
        "cross_sell_product_ids": [7],
        "upsell_products": [{"id": 8}],
        "upsell_product_ids": [8],
        "buy_price": 9.0,
        "vendor_sku": "SECRET",
        "price_monthly_commitment": 11.0,
        "price_annual_monthly_payment": 12.0,
        "price_annual_annual_payment": 13.0,
        "scheduled_buy_price": 8.0,
    }

    payload = handlers._public_shop_product_payload(product, is_vip=False)

    assert payload["id"] == 10
    assert payload["price"] == 25.0
    assert "buy_price" not in payload
    assert "vendor_sku" not in payload
    assert "price_monthly_commitment" not in payload
    assert "price_annual_monthly_payment" not in payload
    assert "price_annual_annual_payment" not in payload
    assert "scheduled_buy_price" not in payload


def test_public_shop_product_payload_uses_vip_price_for_vip_company() -> None:
    product = {
        "id": 11,
        "name": "Switch",
        "price": 30.0,
        "vip_price": 22.0,
    }

    payload = handlers._public_shop_product_payload(product, is_vip=True)

    assert payload["price"] == 22.0


def test_public_shop_product_payload_exposes_sanitized_description_html() -> None:
    product = {
        "id": 12,
        "name": "Firewall",
        "description": "<h3>Specs</h3><script>alert(1)</script><p>Safe</p>",
    }

    payload = handlers._public_shop_product_payload(product, is_vip=False)

    assert payload["description"] == product["description"]
    assert "description_html" in payload
    assert "script" not in payload["description_html"].lower()
    assert "<h3>Specs</h3>" in payload["description_html"]
    assert "<p>Safe</p>" in payload["description_html"]
