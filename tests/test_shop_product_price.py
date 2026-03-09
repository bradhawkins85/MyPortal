"""Tests for the get_product_price helper in app/services/shop.py."""
from decimal import Decimal

from app.services.shop import get_product_price


# ---------------------------------------------------------------------------
# Non-subscription products (no commitment_type)
# ---------------------------------------------------------------------------


def test_non_subscription_returns_standard_price():
    product = {"price": Decimal("49.99"), "vip_price": None, "commitment_type": None}
    assert get_product_price(product) == Decimal("49.99")


def test_non_subscription_vip_returns_vip_price():
    product = {"price": Decimal("49.99"), "vip_price": Decimal("39.99"), "commitment_type": None}
    assert get_product_price(product, is_vip=True) == Decimal("39.99")


def test_non_subscription_vip_no_vip_price_returns_standard():
    product = {"price": Decimal("49.99"), "vip_price": None, "commitment_type": None}
    assert get_product_price(product, is_vip=True) == Decimal("49.99")


# ---------------------------------------------------------------------------
# Monthly commitment products
# ---------------------------------------------------------------------------


def test_monthly_returns_monthly_commitment_price():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": None,
        "commitment_type": "monthly",
        "payment_frequency": "monthly",
        "price_monthly_commitment": Decimal("100.00"),
        "price_annual_monthly_payment": None,
        "price_annual_annual_payment": None,
    }
    assert get_product_price(product) == Decimal("100.00")


def test_monthly_vip_still_returns_monthly_commitment_price():
    """Commitment-specific price takes precedence even for VIP users."""
    product = {
        "price": Decimal("1200.00"),
        "vip_price": Decimal("800.00"),
        "commitment_type": "monthly",
        "payment_frequency": "monthly",
        "price_monthly_commitment": Decimal("100.00"),
    }
    assert get_product_price(product, is_vip=True) == Decimal("100.00")


def test_monthly_no_commitment_price_falls_back_to_standard():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": None,
        "commitment_type": "monthly",
        "payment_frequency": "monthly",
        "price_monthly_commitment": None,
    }
    assert get_product_price(product) == Decimal("1200.00")


def test_monthly_no_commitment_price_vip_falls_back_to_vip_price():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": Decimal("900.00"),
        "commitment_type": "monthly",
        "payment_frequency": "monthly",
        "price_monthly_commitment": None,
    }
    assert get_product_price(product, is_vip=True) == Decimal("900.00")


# ---------------------------------------------------------------------------
# Annual commitment / monthly payment products
# ---------------------------------------------------------------------------


def test_annual_monthly_payment_returns_correct_price():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": None,
        "commitment_type": "annual",
        "payment_frequency": "monthly",
        "price_monthly_commitment": None,
        "price_annual_monthly_payment": Decimal("110.00"),
        "price_annual_annual_payment": Decimal("1100.00"),
    }
    assert get_product_price(product) == Decimal("110.00")


def test_annual_monthly_payment_no_price_falls_back_to_standard():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": None,
        "commitment_type": "annual",
        "payment_frequency": "monthly",
        "price_annual_monthly_payment": None,
        "price_annual_annual_payment": Decimal("1100.00"),
    }
    assert get_product_price(product) == Decimal("1200.00")


# ---------------------------------------------------------------------------
# Annual commitment / annual payment products
# ---------------------------------------------------------------------------


def test_annual_annual_payment_returns_correct_price():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": None,
        "commitment_type": "annual",
        "payment_frequency": "annual",
        "price_monthly_commitment": None,
        "price_annual_monthly_payment": Decimal("110.00"),
        "price_annual_annual_payment": Decimal("1100.00"),
    }
    assert get_product_price(product) == Decimal("1100.00")


def test_annual_annual_payment_no_price_falls_back_to_standard():
    product = {
        "price": Decimal("1200.00"),
        "vip_price": None,
        "commitment_type": "annual",
        "payment_frequency": "annual",
        "price_annual_annual_payment": None,
        "price_annual_monthly_payment": None,
    }
    assert get_product_price(product) == Decimal("1200.00")


def test_annual_annual_vip_still_uses_annual_price():
    """Commitment-specific price takes precedence even for VIP users."""
    product = {
        "price": Decimal("1200.00"),
        "vip_price": Decimal("900.00"),
        "commitment_type": "annual",
        "payment_frequency": "annual",
        "price_annual_annual_payment": Decimal("1100.00"),
    }
    assert get_product_price(product, is_vip=True) == Decimal("1100.00")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_price_returns_zero():
    product = {"commitment_type": None, "payment_frequency": None}
    assert get_product_price(product) == Decimal("0")


def test_price_as_string_is_coerced():
    product = {"price": "29.95", "commitment_type": None, "payment_frequency": None}
    assert get_product_price(product) == Decimal("29.95")


def test_commitment_price_as_string_is_coerced():
    product = {
        "price": "1200.00",
        "commitment_type": "monthly",
        "payment_frequency": "monthly",
        "price_monthly_commitment": "100.00",
    }
    assert get_product_price(product) == Decimal("100.00")
