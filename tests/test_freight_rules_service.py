from decimal import Decimal

from app.services import freight_rules as freight_service


def test_calculate_cart_freight_applies_default_rule_per_dispatch_warehouse():
    cart_items = [
        {
            "product_id": 1,
            "quantity": 3,
            "unit_price": Decimal("100.00"),
            "line_total": Decimal("300.00"),
        }
    ]
    product_lookup = {
        1: {
            "id": 1,
            "stock_nsw": 2,
            "stock_qld": 2,
            "stock_vic": 0,
            "stock_sa": 0,
            "weight": Decimal("1.0"),
            "length": Decimal("20"),
            "width": Decimal("20"),
            "height": Decimal("10"),
        }
    }
    rules = [
        {
            "id": 10,
            "name": "Fallback",
            "is_default": True,
            "conditions": [],
            "freight_amount": Decimal("15.00"),
        }
    ]

    result = freight_service.calculate_cart_freight(cart_items, product_lookup, rules)

    assert result["cart_subtotal"] == Decimal("300.00")
    assert result["freight_total"] == Decimal("30.00")
    assert sorted(
        [entry["dispatch_warehouse"] for entry in result["breakdown"]]
    ) == ["NSW", "QLD"]
    assert all(entry["amount"] == Decimal("15.00") for entry in result["breakdown"])


def test_calculate_cart_freight_prefers_matching_non_default_rule():
    cart_items = [
        {
            "product_id": 5,
            "quantity": 1,
            "unit_price": Decimal("250.00"),
            "line_total": Decimal("250.00"),
        }
    ]
    product_lookup = {
        5: {
            "id": 5,
            "stock_nsw": 1,
            "stock_qld": 0,
            "stock_vic": 0,
            "stock_sa": 0,
            "weight": Decimal("12"),
            "length": Decimal("130"),
            "width": Decimal("40"),
            "height": Decimal("20"),
        }
    }
    rules = [
        {
            "id": 1,
            "name": "Heavy NSW",
            "is_default": False,
            "conditions": [
                {"type": "dispatch_warehouse", "operator": "equals", "value": "NSW"},
                {"type": "item_weight", "operator": "gte", "value": "10"},
            ],
            "freight_amount": Decimal("45.00"),
        },
        {
            "id": 2,
            "name": "Fallback",
            "is_default": True,
            "conditions": [],
            "freight_amount": Decimal("10.00"),
        },
    ]

    result = freight_service.calculate_cart_freight(cart_items, product_lookup, rules)

    assert result["freight_total"] == Decimal("45.00")
    assert result["breakdown"][0]["rule_name"] == "Heavy NSW"
