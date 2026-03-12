"""Tests for the calculate_profit helper in app/services/shop.py."""
from decimal import Decimal

from app.services.shop import calculate_profit


# ---------------------------------------------------------------------------
# No DBP — returns None
# ---------------------------------------------------------------------------


def test_no_buy_price_returns_none():
    product = {"price": Decimal("10.00"), "buy_price": None}
    assert calculate_profit(product) is None


def test_missing_buy_price_key_returns_none():
    product = {"price": Decimal("10.00")}
    assert calculate_profit(product) is None


def test_invalid_buy_price_returns_none():
    product = {"price": Decimal("10.00"), "buy_price": "not-a-number"}
    assert calculate_profit(product) is None


# ---------------------------------------------------------------------------
# Standard price profit calculation
# ---------------------------------------------------------------------------


def test_profit_above_zero():
    # DBP = 10, threshold = 11.00, price = 12.00 → profit = 1.00
    product = {"price": Decimal("12.00"), "buy_price": Decimal("10.00")}
    assert calculate_profit(product) == Decimal("1.00")


def test_profit_exactly_zero():
    # DBP = 10, threshold = 11.00, price = 11.00 → profit = 0.00
    product = {"price": Decimal("11.00"), "buy_price": Decimal("10.00")}
    assert calculate_profit(product) == Decimal("0.00")


def test_profit_below_zero_is_loss():
    # DBP = 10, threshold = 11.00, price = 9.00 → profit = -2.00
    product = {"price": Decimal("9.00"), "buy_price": Decimal("10.00")}
    assert calculate_profit(product) == Decimal("-2.00")


# ---------------------------------------------------------------------------
# VIP price profit calculation
# ---------------------------------------------------------------------------


def test_vip_profit_above_zero():
    # DBP = 10, threshold = 11.00, vip_price = 13.00 → profit = 2.00
    product = {"price": Decimal("15.00"), "vip_price": Decimal("13.00"), "buy_price": Decimal("10.00")}
    assert calculate_profit(product, is_vip=True) == Decimal("2.00")


def test_vip_profit_below_zero():
    # DBP = 10, threshold = 11.00, vip_price = 10.00 → profit = -1.00
    product = {"price": Decimal("15.00"), "vip_price": Decimal("10.00"), "buy_price": Decimal("10.00")}
    assert calculate_profit(product, is_vip=True) == Decimal("-1.00")


def test_vip_profit_no_vip_price_returns_none():
    # No vip_price → None returned for VIP profit
    product = {"price": Decimal("15.00"), "vip_price": None, "buy_price": Decimal("10.00")}
    assert calculate_profit(product, is_vip=True) is None


def test_vip_profit_missing_vip_price_key_returns_none():
    product = {"price": Decimal("15.00"), "buy_price": Decimal("10.00")}
    assert calculate_profit(product, is_vip=True) is None


# ---------------------------------------------------------------------------
# String / type coercion
# ---------------------------------------------------------------------------


def test_string_prices_are_coerced():
    # DBP = "10.00", price = "12.00" → profit = 1.00
    product = {"price": "12.00", "buy_price": "10.00"}
    assert calculate_profit(product) == Decimal("1.00")
