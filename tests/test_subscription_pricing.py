"""Tests for subscription pricing calculations and co-terming logic."""
from datetime import date
from decimal import Decimal

import pytest

from app.services.subscription_pricing import (
    calculate_coterm_price,
    calculate_full_term_end_date,
    get_pricing_explanation,
)


class TestCotermPricing:
    """Test co-term price calculations."""
    
    def test_simple_proration(self):
        """Test simple proration calculation."""
        # Example from spec: Buy on 2025-11-07, end on 2026-01-31
        # Days inclusive = 86, item_price = $365
        # Expected: $1 × 86 = $86
        item_price = Decimal("365.00")
        today = date(2025, 11, 7)
        end_date = date(2026, 1, 31)
        
        result = calculate_coterm_price(item_price, today, end_date)
        
        assert result == Decimal("86.00")
    
    def test_full_year_plus_one(self):
        """Test calculation for 366 days (one day more than a year)."""
        # Example from spec: Buy on 2025-12-01, end on 2026-12-01
        # Days inclusive = 366, item_price = $365
        # Expected: $365/365 × 366 = $366.00
        item_price = Decimal("365.00")
        today = date(2025, 12, 1)
        end_date = date(2026, 12, 1)
        
        result = calculate_coterm_price(item_price, today, end_date)
        
        assert result == Decimal("366.00")
    
    def test_single_day(self):
        """Test calculation for a single day."""
        item_price = Decimal("365.00")
        today = date(2025, 11, 7)
        end_date = date(2025, 11, 7)  # Same day
        
        result = calculate_coterm_price(item_price, today, end_date)
        
        # 1 day inclusive: 365/365 * 1 = 1.00
        assert result == Decimal("1.00")
    
    def test_leap_year_still_divides_by_365(self):
        """Test that leap years still divide by 365 as specified."""
        # Even in a leap year (2024), we divide by 365, not 366
        item_price = Decimal("365.00")
        today = date(2024, 1, 1)  # 2024 is a leap year
        end_date = date(2024, 12, 31)  # 366 days in 2024
        
        # Days from Jan 1 to Dec 31 inclusive in leap year = 366 days
        result = calculate_coterm_price(item_price, today, end_date)
        
        # Should be: 365/365 * 366 = 366.00
        assert result == Decimal("366.00")
    
    def test_rounding_half_up(self):
        """Test that rounding uses ROUND_HALF_UP (banker's rounding)."""
        item_price = Decimal("100.00")
        today = date(2025, 1, 1)
        end_date = date(2025, 2, 10)  # 41 days inclusive
        
        # 100/365 * 41 = 11.232876... should round to 11.23
        result = calculate_coterm_price(item_price, today, end_date)
        
        assert result == Decimal("11.23")
    
    def test_end_date_before_today_raises_error(self):
        """Test that end_date before today raises ValueError."""
        item_price = Decimal("365.00")
        today = date(2025, 11, 7)
        end_date = date(2025, 11, 6)  # Day before
        
        with pytest.raises(ValueError, match="End date must be on or after today"):
            calculate_coterm_price(item_price, today, end_date)
    
    def test_real_world_price_example(self):
        """Test with a realistic product price."""
        # Product costs $1,200 per year
        # Customer buys 91 days before renewal (Oct 1 to Dec 30 inclusive)
        item_price = Decimal("1200.00")
        today = date(2025, 10, 1)
        end_date = date(2025, 12, 30)  # 91 days inclusive
        
        result = calculate_coterm_price(item_price, today, end_date)
        
        # 1200/365 * 91 = 299.178...
        assert result == Decimal("299.18")
    
    def test_various_prices(self):
        """Test with various price points."""
        today = date(2025, 1, 1)
        end_date = date(2025, 12, 31)  # 365 days (full year)
        
        # Full year should equal item price
        for price_str in ["99.99", "500.00", "1000.00", "2499.99"]:
            item_price = Decimal(price_str)
            result = calculate_coterm_price(item_price, today, end_date)
            # Full year (365 days) should give us the full price
            assert result == item_price


class TestFullTermEndDate:
    """Test full-term end date calculations."""
    
    def test_default_365_days(self):
        """Test default 365-day term."""
        start_date = date(2025, 1, 1)
        result = calculate_full_term_end_date(start_date)
        
        # 365 days from Jan 1, 2025
        assert result == date(2026, 1, 1)
    
    def test_custom_term_days(self):
        """Test custom term length."""
        start_date = date(2025, 1, 1)
        result = calculate_full_term_end_date(start_date, term_days=30)
        
        assert result == date(2025, 1, 31)
    
    def test_leap_year_boundary(self):
        """Test term across leap year boundary."""
        start_date = date(2024, 1, 1)  # Leap year
        result = calculate_full_term_end_date(start_date, term_days=366)
        
        # 366 days from Jan 1, 2024 = Jan 1, 2025
        assert result == date(2025, 1, 1)


class TestPricingExplanation:
    """Test pricing explanation generation."""
    
    def test_explanation_fields(self):
        """Test that explanation contains all required fields."""
        item_price = Decimal("365.00")
        today = date(2025, 11, 7)
        end_date = date(2026, 1, 31)
        coterm_price = calculate_coterm_price(item_price, today, end_date)
        
        explanation = get_pricing_explanation(
            item_price, today, end_date, coterm_price
        )
        
        assert "days_inclusive" in explanation
        assert "daily_rate" in explanation
        assert "daily_rate_formatted" in explanation
        assert "item_price" in explanation
        assert "item_price_formatted" in explanation
        assert "coterm_price" in explanation
        assert "coterm_price_formatted" in explanation
        assert "formula" in explanation
        assert "end_date" in explanation
    
    def test_explanation_accuracy(self):
        """Test that explanation calculations are accurate."""
        item_price = Decimal("365.00")
        today = date(2025, 11, 7)
        end_date = date(2026, 1, 31)
        coterm_price = Decimal("86.00")
        
        explanation = get_pricing_explanation(
            item_price, today, end_date, coterm_price
        )
        
        assert explanation["days_inclusive"] == 86
        assert explanation["daily_rate"] == Decimal("1.00")
        assert explanation["item_price"] == item_price
        assert explanation["coterm_price"] == coterm_price
        assert explanation["end_date"] == end_date
    
    def test_explanation_formatting(self):
        """Test that formatted strings look correct."""
        item_price = Decimal("1200.00")
        today = date(2025, 1, 1)
        end_date = date(2025, 4, 1)  # 91 days
        coterm_price = calculate_coterm_price(item_price, today, end_date)
        
        explanation = get_pricing_explanation(
            item_price, today, end_date, coterm_price
        )
        
        assert "$1200.00" in explanation["item_price_formatted"]
        assert "$" in explanation["daily_rate_formatted"]
        assert "$" in explanation["coterm_price_formatted"]
        assert "÷ 365" in explanation["formula"]
        assert "91 days" in explanation["formula"]
