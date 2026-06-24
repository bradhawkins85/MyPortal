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


def test_calculate_cart_freight_charges_once_for_multiple_items_in_same_shipment():
    cart_items = [
        {
            "product_id": 1,
            "quantity": 1,
            "unit_price": Decimal("100.00"),
            "line_total": Decimal("100.00"),
        },
        {
            "product_id": 2,
            "quantity": 2,
            "unit_price": Decimal("50.00"),
            "line_total": Decimal("100.00"),
        },
    ]
    product_lookup = {
        1: {
            "id": 1,
            "stock_nsw": 5,
            "stock_qld": 0,
            "stock_vic": 0,
            "stock_sa": 0,
            "weight": Decimal("1.0"),
            "length": Decimal("20"),
            "width": Decimal("20"),
            "height": Decimal("10"),
        },
        2: {
            "id": 2,
            "stock_nsw": 5,
            "stock_qld": 0,
            "stock_vic": 0,
            "stock_sa": 0,
            "weight": Decimal("1.0"),
            "length": Decimal("20"),
            "width": Decimal("20"),
            "height": Decimal("10"),
        },
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

    assert result["freight_total"] == Decimal("15.00")
    assert len(result["breakdown"]) == 1
    assert result["breakdown"][0]["dispatch_warehouse"] == "NSW"
    assert result["breakdown"][0]["shipment_quantity"] == 3


def test_calculate_cart_freight_charges_separate_qld_and_wa_shipments():
    cart_items = [
        {
            "product_id": 1,
            "quantity": 1,
            "unit_price": Decimal("100.00"),
            "line_total": Decimal("100.00"),
        },
        {
            "product_id": 2,
            "quantity": 1,
            "unit_price": Decimal("200.00"),
            "line_total": Decimal("200.00"),
        },
    ]
    product_lookup = {
        1: {
            "id": 1,
            "stock_nsw": 0,
            "stock_qld": 4,
            "stock_vic": 0,
            "stock_sa": 0,
            "stock_wa": 0,
            "weight": Decimal("1.0"),
            "length": Decimal("20"),
            "width": Decimal("20"),
            "height": Decimal("10"),
        },
        2: {
            "id": 2,
            "stock_nsw": 0,
            "stock_qld": 0,
            "stock_vic": 0,
            "stock_sa": 0,
            "stock_wa": 3,
            "weight": Decimal("1.0"),
            "length": Decimal("20"),
            "width": Decimal("20"),
            "height": Decimal("10"),
        },
    }
    rules = [
        {
            "id": 1,
            "name": "QLD freight",
            "is_default": False,
            "conditions": [
                {"type": "dispatch_warehouse", "operator": "equals", "value": "QLD"},
            ],
            "freight_amount": Decimal("20.00"),
        },
        {
            "id": 2,
            "name": "WA freight",
            "is_default": False,
            "conditions": [
                {"type": "dispatch_warehouse", "operator": "equals", "value": "WA"},
            ],
            "freight_amount": Decimal("35.00"),
        },
    ]

    result = freight_service.calculate_cart_freight(cart_items, product_lookup, rules)

    assert result["freight_total"] == Decimal("55.00")
    assert sorted(
        (entry["dispatch_warehouse"], entry["amount"])
        for entry in result["breakdown"]
    ) == [("QLD", Decimal("20.00")), ("WA", Decimal("35.00"))]


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


def test_calculate_cart_freight_applies_multiple_matching_rules_in_priority_order():
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
            "name": "Large item handling",
            "is_default": False,
            "conditions": [
                {"type": "item_size", "operator": "equals", "value": "large"},
            ],
            "freight_amount": Decimal("5.00"),
        },
        {
            "id": 3,
            "name": "Fallback",
            "is_default": True,
            "conditions": [],
            "freight_amount": Decimal("10.00"),
        },
    ]

    result = freight_service.calculate_cart_freight(cart_items, product_lookup, rules)

    assert result["freight_total"] == Decimal("50.00")
    assert result["breakdown"][0]["rule_name"] == "Heavy NSW"
    assert result["breakdown"][0]["applied_rule_ids"] == [1, 2]


def test_calculate_cart_freight_stops_processing_on_matching_stop_rule():
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
            "stop_processing": True,
            "conditions": [
                {"type": "dispatch_warehouse", "operator": "equals", "value": "NSW"},
                {"type": "item_weight", "operator": "gte", "value": "10"},
            ],
            "freight_amount": Decimal("45.00"),
        },
        {
            "id": 2,
            "name": "Large item handling",
            "is_default": False,
            "conditions": [
                {"type": "item_size", "operator": "equals", "value": "large"},
            ],
            "freight_amount": Decimal("5.00"),
        },
        {
            "id": 3,
            "name": "Fallback",
            "is_default": True,
            "conditions": [],
            "freight_amount": Decimal("10.00"),
        },
    ]

    result = freight_service.calculate_cart_freight(cart_items, product_lookup, rules)

    assert result["freight_total"] == Decimal("45.00")
    assert result["breakdown"][0]["applied_rule_ids"] == [1]
