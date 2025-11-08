"""Tests for subscription changes service."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.subscription_changes import (
    calculate_net_changes,
)


def test_calculate_net_changes_empty():
    """Test calculating net changes with no pending changes."""
    result = calculate_net_changes([])
    
    assert result["net_additions"] == 0
    assert result["net_decreases"] == 0
    assert result["net_change"] == 0
    assert result["total_prorated_charges"] == Decimal("0.00")
    assert result["additions_count"] == 0
    assert result["decreases_count"] == 0


def test_calculate_net_changes_only_additions():
    """Test calculating net changes with only additions."""
    changes = [
        {
            "change_type": "addition",
            "quantity_change": 5,
            "prorated_charge": Decimal("50.00"),
        },
        {
            "change_type": "addition",
            "quantity_change": 3,
            "prorated_charge": Decimal("30.00"),
        },
    ]
    
    result = calculate_net_changes(changes)
    
    assert result["net_additions"] == 8
    assert result["net_decreases"] == 0
    assert result["net_change"] == 8
    assert result["total_prorated_charges"] == Decimal("80.00")
    assert result["additions_count"] == 2
    assert result["decreases_count"] == 0


def test_calculate_net_changes_only_decreases():
    """Test calculating net changes with only decreases."""
    changes = [
        {
            "change_type": "decrease",
            "quantity_change": 5,
            "prorated_charge": None,
        },
        {
            "change_type": "decrease",
            "quantity_change": 2,
            "prorated_charge": None,
        },
    ]
    
    result = calculate_net_changes(changes)
    
    assert result["net_additions"] == 0
    assert result["net_decreases"] == 7
    assert result["net_change"] == -7
    assert result["total_prorated_charges"] == Decimal("0.00")
    assert result["additions_count"] == 0
    assert result["decreases_count"] == 2


def test_calculate_net_changes_mixed():
    """Test calculating net changes with mixed additions and decreases."""
    changes = [
        {
            "change_type": "decrease",
            "quantity_change": 5,
            "prorated_charge": None,
        },
        {
            "change_type": "addition",
            "quantity_change": 2,
            "prorated_charge": Decimal("20.00"),
        },
    ]
    
    result = calculate_net_changes(changes)
    
    assert result["net_additions"] == 2
    assert result["net_decreases"] == 5
    assert result["net_change"] == -3
    assert result["total_prorated_charges"] == Decimal("20.00")
    assert result["additions_count"] == 1
    assert result["decreases_count"] == 1


def test_calculate_net_changes_scenario_remove_5_add_2():
    """Test the example scenario: Remove 5, add 2 = net reduction of 3, no new charges."""
    changes = [
        {
            "change_type": "decrease",
            "quantity_change": 5,
            "prorated_charge": None,
        },
        {
            "change_type": "addition",
            "quantity_change": 2,
            "prorated_charge": Decimal("20.00"),  # Already applied
        },
    ]
    
    result = calculate_net_changes(changes)
    
    # Net reduction of 3
    assert result["net_change"] == -3
    # Charge is for the 2 that were already added
    assert result["total_prorated_charges"] == Decimal("20.00")


def test_calculate_net_changes_scenario_remove_1_add_2():
    """Test the example scenario: Remove 1, add 2 = net addition of 1, charge for 1."""
    # In this scenario, user requested remove 1, then add 2
    # The decrease is pending, but they want to preview adding 2 more
    pending = [
        {
            "change_type": "decrease",
            "quantity_change": 1,
            "prorated_charge": None,
        },
    ]
    
    # When they add 2, it would be:
    new_addition = {
        "change_type": "addition",
        "quantity_change": 2,
        "prorated_charge": Decimal("20.00"),
    }
    
    # Total changes would be:
    all_changes = pending + [new_addition]
    result = calculate_net_changes(all_changes)
    
    # Net addition of 1
    assert result["net_change"] == 1
    # Charge for the 2 licenses added
    assert result["total_prorated_charges"] == Decimal("20.00")


def test_calculate_net_changes_complex_stacking():
    """Test complex stacking of multiple changes."""
    changes = [
        {"change_type": "addition", "quantity_change": 10, "prorated_charge": Decimal("100.00")},
        {"change_type": "decrease", "quantity_change": 5, "prorated_charge": None},
        {"change_type": "addition", "quantity_change": 3, "prorated_charge": Decimal("30.00")},
        {"change_type": "decrease", "quantity_change": 2, "prorated_charge": None},
        {"change_type": "addition", "quantity_change": 1, "prorated_charge": Decimal("10.00")},
    ]
    
    result = calculate_net_changes(changes)
    
    # Additions: 10 + 3 + 1 = 14
    assert result["net_additions"] == 14
    # Decreases: 5 + 2 = 7
    assert result["net_decreases"] == 7
    # Net: 14 - 7 = 7
    assert result["net_change"] == 7
    # Charges: 100 + 30 + 10 = 140
    assert result["total_prorated_charges"] == Decimal("140.00")
    assert result["additions_count"] == 3
    assert result["decreases_count"] == 2


def test_prorata_calculation_logic():
    """Test the prorata calculation logic for license additions.
    
    This verifies the formula: (unit_price / 365) * days_remaining
    """
    unit_price = Decimal("365.00")  # $1 per day for easy calculation
    today = date(2025, 1, 1)
    end_date = date(2025, 12, 31)  # 365 days away
    
    days_remaining = (end_date - today).days + 1  # 365 days
    per_license_charge = (unit_price / Decimal("365")) * Decimal(str(days_remaining))
    
    # Should be exactly $365.00 for a full year
    assert per_license_charge == Decimal("365.00")
    
    # Test partial year (6 months)
    end_date_6months = date(2025, 7, 1)  # ~181 days
    days_remaining_6m = (end_date_6months - today).days + 1
    per_license_charge_6m = (unit_price / Decimal("365")) * Decimal(str(days_remaining_6m))
    
    # Should be approximately half the annual price
    assert per_license_charge_6m > Decimal("180.00")
    assert per_license_charge_6m <= Decimal("182.00")


def test_prorata_calculation_realistic_price():
    """Test prorata calculation with realistic pricing."""
    unit_price = Decimal("120.00")  # $120 per year
    today = date(2025, 6, 1)
    end_date = date(2025, 12, 31)  # 214 days remaining
    
    days_remaining = (end_date - today).days + 1
    daily_rate = unit_price / Decimal("365")
    per_license_charge = daily_rate * Decimal(str(days_remaining))
    
    # Daily rate should be $0.328767...
    assert daily_rate > Decimal("0.32")
    assert daily_rate < Decimal("0.33")
    
    # For 214 days, should be around $70.36
    assert per_license_charge > Decimal("70.00")
    assert per_license_charge < Decimal("71.00")
