"""Tests for subscription changes service."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.subscription_changes import (
    calculate_net_changes,
    preview_subscription_change,
    apply_subscription_addition,
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


def test_calculate_chargeable_licenses_no_pending():
    """Test calculating chargeable licenses with no pending changes."""
    from app.services.subscription_changes import calculate_chargeable_licenses
    
    # Current quantity: 5, adding 3 more, no pending changes
    # Should charge for all 3 licenses
    chargeable = calculate_chargeable_licenses(
        current_quantity=5,
        quantity_to_add=3,
        pending_net_change=0,
    )
    assert chargeable == 3


def test_calculate_chargeable_licenses_with_pending_decrease():
    """Test calculating chargeable licenses with pending decrease.
    
    Scenario: Start with 2 licenses, remove 1 (pending), add 1 more
    - Current quantity: 2
    - Pending decrease: 1
    - Quantity at term end without new addition: 2 - 1 = 1
    - Adding 1: would result in 2 at term end
    - Since contracted quantity is 2, no charge for the addition
    """
    from app.services.subscription_changes import calculate_chargeable_licenses
    
    chargeable = calculate_chargeable_licenses(
        current_quantity=2,
        quantity_to_add=1,
        pending_net_change=-1,  # net decrease of 1
    )
    assert chargeable == 0


def test_calculate_chargeable_licenses_partial_charge():
    """Test calculating chargeable licenses with partial charge scenario.
    
    Scenario: Start with 5 licenses, remove 3 (pending), add 4 more
    - Current quantity: 5
    - Pending decrease: 3
    - Quantity at term end without new addition: 5 - 3 = 2
    - Adding 4: would result in 6 at term end
    - Since contracted quantity is 5, charge for 6 - 5 = 1 license
    """
    from app.services.subscription_changes import calculate_chargeable_licenses
    
    chargeable = calculate_chargeable_licenses(
        current_quantity=5,
        quantity_to_add=4,
        pending_net_change=-3,
    )
    assert chargeable == 1


def test_calculate_chargeable_licenses_with_prior_additions():
    """Test calculating chargeable licenses after prior additions.
    
    Scenario: Start with 1 license, add 2 (already applied), then add 2 more
    - Current quantity: 3 (1 original + 2 added)
    - Applied additions: 2
    - Original contracted quantity: 3 - 2 = 1
    - No pending changes
    - Adding 2 more: would result in 5 at term end
    - Since original contracted quantity is 1, charge for 5 - 1 = 4 licenses
    """
    from app.services.subscription_changes import calculate_chargeable_licenses
    
    chargeable = calculate_chargeable_licenses(
        current_quantity=3,
        quantity_to_add=2,
        pending_net_change=0,
        applied_additions=2,
    )
    assert chargeable == 4


def test_calculate_chargeable_licenses_exact_contracted_amount():
    """Test that adding licenses up to contracted amount has no charge.
    
    Scenario: Start with 10 licenses, remove 5 (pending), add 5
    - Current quantity: 10
    - Pending decrease: 5
    - Quantity at term end without new addition: 10 - 5 = 5
    - Adding 5: would result in 10 at term end
    - Since contracted quantity is 10, no charge for the 5 licenses
    """
    from app.services.subscription_changes import calculate_chargeable_licenses
    
    chargeable = calculate_chargeable_licenses(
        current_quantity=10,
        quantity_to_add=5,
        pending_net_change=-5,
    )
    assert chargeable == 0


@pytest.mark.asyncio
async def test_preview_addition_with_no_pending_changes(monkeypatch):
    """Test preview of addition when there are no pending changes.
    
    All added licenses should be charged since there are no pending decreases.
    """
    # Mock the repositories
    subscription_id = str(uuid4())
    mock_subscription = {
        "id": subscription_id,
        "quantity": 5,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
    }
    
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return []
    
    async def mock_get_applied_additions(sub_id):
        return 0
    
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "get_applied_additions_for_subscription", mock_get_applied_additions)
    
    # Preview adding 3 licenses
    preview = await preview_subscription_change(subscription_id, 3, "addition")
    
    # Should charge for all 3 licenses
    assert preview["current_quantity"] == 5
    assert preview["requested_change"] == 3
    assert preview["new_quantity_at_term_end"] == 8
    assert preview["prorated_charge"] > Decimal("0")
    assert preview["prorated_explanation"]["chargeable_licenses"] == 3
    assert preview["prorated_explanation"]["free_licenses"] == 0


@pytest.mark.asyncio
async def test_preview_addition_with_pending_decrease_no_charge(monkeypatch):
    """Test preview of addition when pending decrease offsets the addition.
    
    Scenario: 2 licenses, decrease 1 pending, add 1 = no charge
    """
    subscription_id = str(uuid4())
    mock_subscription = {
        "id": subscription_id,
        "quantity": 2,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
    }
    
    mock_pending_changes = [
        {
            "id": str(uuid4()),
            "subscription_id": subscription_id,
            "change_type": "decrease",
            "quantity_change": 1,
            "prorated_charge": None,
            "status": "pending",
        }
    ]
    
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return mock_pending_changes
    
    async def mock_get_applied_additions(sub_id):
        return 0
    
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "get_applied_additions_for_subscription", mock_get_applied_additions)
    
    # Preview adding 1 license
    preview = await preview_subscription_change(subscription_id, 1, "addition")
    
    # Should NOT charge since quantity at term end equals contracted quantity
    assert preview["current_quantity"] == 2
    assert preview["requested_change"] == 1
    assert preview["new_quantity_at_term_end"] == 2  # 2 - 1 + 1
    assert preview["prorated_charge"] == Decimal("0.00")
    assert preview["prorated_explanation"]["chargeable_licenses"] == 0
    assert preview["prorated_explanation"]["free_licenses"] == 1


@pytest.mark.asyncio
async def test_preview_addition_with_pending_decrease_partial_charge(monkeypatch):
    """Test preview of addition when only some licenses need to be charged.
    
    Scenario: 5 licenses, decrease 3 pending, add 4 = charge for 2
    """
    subscription_id = str(uuid4())
    mock_subscription = {
        "id": subscription_id,
        "quantity": 5,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
    }
    
    mock_pending_changes = [
        {
            "id": str(uuid4()),
            "subscription_id": subscription_id,
            "change_type": "decrease",
            "quantity_change": 3,
            "prorated_charge": None,
            "status": "pending",
        }
    ]
    
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return mock_pending_changes
    
    async def mock_get_applied_additions(sub_id):
        return 0
    
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "get_applied_additions_for_subscription", mock_get_applied_additions)
    
    # Preview adding 4 licenses
    preview = await preview_subscription_change(subscription_id, 4, "addition")
    
    # Should charge for 1 license: (5 - 3 + 4) - 5 = 1
    assert preview["current_quantity"] == 5
    assert preview["requested_change"] == 4
    assert preview["new_quantity_at_term_end"] == 6  # 5 - 3 + 4
    assert preview["prorated_charge"] > Decimal("0")
    assert preview["prorated_explanation"]["chargeable_licenses"] == 1
    assert preview["prorated_explanation"]["free_licenses"] == 3


def test_calculate_chargeable_licenses_issue_scenario():
    """Test the reported issue scenario.
    
    Scenario from issue:
    - Start with 2 licenses
    - Remove 1 (pending) -> 1 at renewal
    - Add 1 (replacement) -> Should be free (uses spare from pending removal)
    - Add 1 more (above original count) -> BUG: Should charge but doesn't
    """
    from app.services.subscription_changes import calculate_chargeable_licenses
    
    # Step 1: Start with 2 licenses, remove 1 pending
    current_quantity = 2
    pending_net_change = -1  # -1 from the removal
    applied_additions = 0  # No applied additions yet
    
    # Step 2: Add 1 (replacement)
    chargeable_first_add = calculate_chargeable_licenses(
        current_quantity=current_quantity,
        quantity_to_add=1,
        pending_net_change=pending_net_change,
        applied_additions=applied_additions,
    )
    assert chargeable_first_add == 0, "First addition should be free (within contracted quantity)"
    
    # Step 3: After first addition is applied, current_quantity becomes 3
    # But the original contracted quantity is still 2
    current_quantity_after_first_add = 3
    # Pending removal is still there
    pending_net_change_after_first_add = -1
    # Now we have 1 applied addition
    applied_additions_after_first_add = 1
    
    # Now add 1 more (above original count of 2)
    chargeable_second_add = calculate_chargeable_licenses(
        current_quantity=current_quantity_after_first_add,
        quantity_to_add=1,
        pending_net_change=pending_net_change_after_first_add,
        applied_additions=applied_additions_after_first_add,
    )
    
    # At term end: 3 (current) - 1 (pending decrease) + 1 (new add) = 3
    # Original contracted quantity: 3 - 1 (applied additions) = 2
    # Should charge for: 3 - 2 = 1
    assert chargeable_second_add == 1, "Second addition should charge for 1 license (exceeds original contracted quantity)"


@pytest.mark.asyncio
async def test_preview_issue_scenario_sequential_additions(monkeypatch):
    """Test the reported issue using preview with sequential additions.
    
    This simulates:
    1. Start with 2 licenses, pending decrease of 1
    2. Add 1 license (applied immediately, no charge due to pending decrease)
    3. Add 1 more license (should charge as it exceeds original contracted quantity)
    """
    subscription_id = str(uuid4())
    
    # Mock subscription starting with 2 licenses
    mock_subscription = {
        "id": subscription_id,
        "quantity": 2,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
    }
    
    # Mock pending decrease of 1
    mock_pending_changes = [
        {
            "id": str(uuid4()),
            "subscription_id": subscription_id,
            "change_type": "decrease",
            "quantity_change": 1,
            "prorated_charge": None,
            "status": "pending",
        }
    ]
    
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return mock_pending_changes
    
    # Track applied additions
    applied_additions_count = 0
    
    async def mock_get_applied_additions(sub_id):
        return applied_additions_count
    
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "get_applied_additions_for_subscription", mock_get_applied_additions)
    
    # Preview adding 1 license (replacement)
    preview1 = await preview_subscription_change(subscription_id, 1, "addition")
    assert preview1["prorated_charge"] == Decimal("0.00"), "First addition should be free"
    assert preview1["prorated_explanation"]["chargeable_licenses"] == 0
    assert preview1["prorated_explanation"]["free_licenses"] == 1
    
    # Simulate that the first addition was applied
    mock_subscription["quantity"] = 3  # Now 3 licenses (2 + 1)
    applied_additions_count = 1  # Update the applied additions count
    
    # Preview adding 1 more license (above original count of 2)
    preview2 = await preview_subscription_change(subscription_id, 1, "addition")
    
    # This should charge for 1 license
    # At term end: 3 (current) - 1 (pending decrease) + 1 (new add) = 3
    # Original contracted quantity: 2
    # Should charge for: 3 - 2 = 1
    assert preview2["prorated_charge"] > Decimal("0.00"), "Second addition should have a charge"
    assert preview2["prorated_explanation"]["chargeable_licenses"] == 1, "Should charge for 1 license"
    assert preview2["prorated_explanation"]["free_licenses"] == 0
