from app import main


def test_strip_internal_shop_product_fields_removes_sensitive_fields() -> None:
    products = [
        {
            "id": 1,
            "name": "Widget",
            "sku": "W-1",
            "vendor_sku": "INT-001",
            "buy_price": 9.99,
            "price": 19.99,
        }
    ]

    result = main._strip_internal_shop_product_fields(products)

    assert "vendor_sku" not in result[0]
    assert "buy_price" not in result[0]
    assert result[0]["id"] == 1
    assert result[0]["price"] == 19.99


def test_strip_internal_shop_product_fields_keeps_original_input_unchanged() -> None:
    product = {
        "id": 2,
        "vendor_sku": "INT-002",
        "buy_price": 5.5,
        "price": 10.0,
    }

    _ = main._strip_internal_shop_product_fields([product])

    assert "vendor_sku" in product
    assert "buy_price" in product
