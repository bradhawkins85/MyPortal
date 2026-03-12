"""Tests for the is_price_below_dbp_threshold helper in app/services/shop.py."""
from decimal import Decimal

from app.services.shop import is_price_below_dbp_threshold


# ---------------------------------------------------------------------------
# No DBP — never hidden
# ---------------------------------------------------------------------------


def test_no_buy_price_returns_false():
    product = {"price": Decimal("10.00"), "vip_price": None, "buy_price": None}
    assert is_price_below_dbp_threshold(product) is False


def test_missing_buy_price_key_returns_false():
    product = {"price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product) is False


def test_zero_buy_price_returns_false():
    product = {"price": Decimal("1.00"), "buy_price": Decimal("0.00")}
    assert is_price_below_dbp_threshold(product) is False


# ---------------------------------------------------------------------------
# Standard price vs DBP threshold
# ---------------------------------------------------------------------------


def test_price_above_threshold_returns_false():
    # DBP = 10, threshold = 11.00, price = 12.00 → OK
    product = {"price": Decimal("12.00"), "vip_price": None, "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product) is False


def test_price_equal_threshold_returns_false():
    # DBP = 10, threshold = 11.00, price = 11.00 → exactly at threshold → OK
    product = {"price": Decimal("11.00"), "vip_price": None, "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product) is False


def test_price_below_threshold_returns_true():
    # DBP = 10, threshold = 11.00, price = 10.50 → below → hidden
    product = {"price": Decimal("10.50"), "vip_price": None, "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product) is True


def test_price_way_below_threshold_returns_true():
    # DBP = 100, threshold = 110.00, price = 50.00 → hidden
    product = {"price": Decimal("50.00"), "vip_price": None, "buy_price": Decimal("100.00")}
    assert is_price_below_dbp_threshold(product) is True


# ---------------------------------------------------------------------------
# VIP price vs DBP threshold
# ---------------------------------------------------------------------------


def test_vip_price_above_threshold_returns_false():
    # DBP = 10, threshold = 11.00, vip_price = 11.50 → OK
    product = {"price": Decimal("15.00"), "vip_price": Decimal("11.50"), "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product, is_vip=True) is False


def test_vip_price_below_threshold_returns_true():
    # DBP = 10, threshold = 11.00, vip_price = 10.50 → hidden for VIP
    product = {"price": Decimal("15.00"), "vip_price": Decimal("10.50"), "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product, is_vip=True) is True


def test_vip_no_vip_price_falls_back_to_standard():
    # No vip_price set → effective price is standard price
    # DBP = 10, threshold = 11.00, price = 12.00 → OK even for VIP
    product = {"price": Decimal("12.00"), "vip_price": None, "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product, is_vip=True) is False


def test_non_vip_uses_standard_price_not_vip_price():
    # For non-VIP, vip_price should not affect the check
    # DBP = 10, threshold = 11.00, price = 12.00, vip_price = 9.00 → non-VIP sees 12.00 → OK
    product = {"price": Decimal("12.00"), "vip_price": Decimal("9.00"), "buy_price": Decimal("10.00")}
    assert is_price_below_dbp_threshold(product, is_vip=False) is False


# ---------------------------------------------------------------------------
# String / type coercion
# ---------------------------------------------------------------------------


def test_string_prices_are_coerced():
    product = {"price": "10.50", "vip_price": None, "buy_price": "10.00"}
    assert is_price_below_dbp_threshold(product) is True


def test_invalid_buy_price_returns_false():
    product = {"price": Decimal("10.00"), "buy_price": "not-a-number"}
    assert is_price_below_dbp_threshold(product) is False
