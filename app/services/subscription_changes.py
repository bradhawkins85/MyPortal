"""Service for managing live subscription changes with prorata pricing."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.repositories import subscription_change_requests as change_requests_repo
from app.repositories import subscriptions as subscriptions_repo
from app.services.subscription_pricing import calculate_coterm_price


def calculate_net_changes(
    pending_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate the net effect of pending changes.
    
    Returns:
        - net_additions: Total licenses to be added
        - net_decreases: Total licenses to be removed
        - net_change: Net change in license count
        - total_prorated_charges: Total charges for additions
        - additions_count: Number of addition requests
        - decreases_count: Number of decrease requests
    """
    net_additions = 0
    net_decreases = 0
    total_prorated_charges = Decimal("0.00")
    additions_count = 0
    decreases_count = 0
    
    for change in pending_changes:
        if change["change_type"] == "addition":
            net_additions += change["quantity_change"]
            additions_count += 1
            if change.get("prorated_charge"):
                total_prorated_charges += change["prorated_charge"]
        elif change["change_type"] == "decrease":
            net_decreases += change["quantity_change"]
            decreases_count += 1
    
    net_change = net_additions - net_decreases
    
    return {
        "net_additions": net_additions,
        "net_decreases": net_decreases,
        "net_change": net_change,
        "total_prorated_charges": total_prorated_charges,
        "additions_count": additions_count,
        "decreases_count": decreases_count,
    }


async def preview_subscription_change(
    subscription_id: str,
    quantity_change: int,
    change_type: str,
) -> dict[str, Any]:
    """Preview the impact of a subscription change request.
    
    Calculates prorata charges for additions and shows net impact with pending changes.
    
    Args:
        subscription_id: The subscription to modify
        quantity_change: Number of licenses to add or remove (positive number)
        change_type: 'addition' or 'decrease'
        
    Returns:
        Dictionary with:
        - current_quantity: Current license count
        - requested_change: The requested change
        - change_type: addition or decrease
        - pending_changes: List of existing pending changes
        - net_impact: Net change after applying this request with pending ones
        - prorated_charge: Charge for this addition (None for decreases)
        - prorated_explanation: Human-readable pricing explanation
        - new_quantity_at_term_end: Final quantity after all changes
    """
    # Get the subscription
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise ValueError("Subscription not found")
    
    current_quantity = subscription["quantity"]
    unit_price = subscription["unit_price"]
    end_date = subscription["end_date"]
    
    # Get pending changes
    pending_changes = await change_requests_repo.list_pending_changes_for_subscription(
        subscription_id
    )
    
    # Calculate net effect of existing pending changes
    net_current = calculate_net_changes(pending_changes)
    
    # Calculate prorata charge for additions
    prorated_charge = None
    prorated_explanation = None
    
    if change_type == "addition":
        # Calculate prorated price for each additional license
        today = date.today()
        per_license_charge = calculate_coterm_price(unit_price, today, end_date)
        prorated_charge = per_license_charge * quantity_change
        
        days_remaining = (end_date - today).days + 1
        prorated_explanation = {
            "per_license_charge": per_license_charge,
            "quantity": quantity_change,
            "total_charge": prorated_charge,
            "days_remaining": days_remaining,
            "unit_price": unit_price,
            "end_date": end_date,
            "formula": (
                f"({quantity_change} licenses × ${unit_price:.2f} ÷ 365) × {days_remaining} days = "
                f"${prorated_charge:.2f}"
            ),
        }
    
    # Calculate new net impact including this change
    if change_type == "addition":
        new_net_additions = net_current["net_additions"] + quantity_change
        new_net_decreases = net_current["net_decreases"]
        new_total_charges = net_current["total_prorated_charges"] + (prorated_charge or Decimal("0"))
    else:  # decrease
        new_net_additions = net_current["net_additions"]
        new_net_decreases = net_current["net_decreases"] + quantity_change
        new_total_charges = net_current["total_prorated_charges"]
    
    new_net_change = new_net_additions - new_net_decreases
    new_quantity_at_term_end = current_quantity + new_net_change
    
    return {
        "current_quantity": current_quantity,
        "requested_change": quantity_change,
        "change_type": change_type,
        "pending_changes": pending_changes,
        "current_net_impact": net_current,
        "new_net_additions": new_net_additions,
        "new_net_decreases": new_net_decreases,
        "new_net_change": new_net_change,
        "new_total_charges": new_total_charges,
        "new_quantity_at_term_end": new_quantity_at_term_end,
        "prorated_charge": prorated_charge,
        "prorated_explanation": prorated_explanation,
        "end_date": end_date,
    }


async def apply_subscription_addition(
    subscription_id: str,
    quantity_to_add: int,
    requested_by: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """Apply a license addition immediately with prorata charge.
    
    Increases the subscription quantity immediately and creates a change request
    record with the prorated charge.
    
    Args:
        subscription_id: The subscription to modify
        quantity_to_add: Number of licenses to add (must be positive)
        requested_by: User ID making the request
        notes: Optional notes about the change
        
    Returns:
        Dictionary with:
        - subscription: Updated subscription
        - change_request: Created change request record
        - prorated_charge: Charge for this addition
    """
    if quantity_to_add <= 0:
        raise ValueError("Quantity to add must be positive")
    
    # Get preview to calculate charges
    preview = await preview_subscription_change(
        subscription_id, quantity_to_add, "addition"
    )
    
    prorated_charge = preview["prorated_charge"]
    
    # Update the subscription quantity immediately
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    new_quantity = subscription["quantity"] + quantity_to_add
    
    await subscriptions_repo.update_subscription(
        subscription_id,
        quantity=new_quantity,
    )
    
    # Create a change request record marked as applied
    change_request = await change_requests_repo.create_change_request(
        subscription_id=subscription_id,
        change_type="addition",
        quantity_change=quantity_to_add,
        requested_by=requested_by,
        prorated_charge=prorated_charge,
        notes=notes,
    )
    
    # Mark it as applied immediately
    await change_requests_repo.apply_change_request(change_request["id"])
    
    logger.info(
        f"Applied subscription addition: {quantity_to_add} licenses added to {subscription_id}, "
        f"prorated charge: ${prorated_charge:.2f}"
    )
    
    # Get updated subscription
    updated_subscription = await subscriptions_repo.get_subscription(subscription_id)
    
    return {
        "subscription": updated_subscription,
        "change_request": change_request,
        "prorated_charge": prorated_charge,
        "preview": preview,
    }


async def request_subscription_decrease(
    subscription_id: str,
    quantity_to_decrease: int,
    requested_by: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """Request a license decrease to be applied at term end.
    
    Creates a pending change request that will be processed when the subscription
    term ends.
    
    Args:
        subscription_id: The subscription to modify
        quantity_to_decrease: Number of licenses to remove (must be positive)
        requested_by: User ID making the request
        notes: Optional notes about the change
        
    Returns:
        Dictionary with:
        - change_request: Created change request record
        - preview: Preview of the net impact
    """
    if quantity_to_decrease <= 0:
        raise ValueError("Quantity to decrease must be positive")
    
    # Get preview to show impact
    preview = await preview_subscription_change(
        subscription_id, quantity_to_decrease, "decrease"
    )
    
    # Validate that decrease doesn't bring quantity below zero
    if preview["new_quantity_at_term_end"] < 0:
        raise ValueError(
            f"Cannot decrease by {quantity_to_decrease} licenses. "
            f"This would result in a negative quantity at term end."
        )
    
    # Create a pending change request
    change_request = await change_requests_repo.create_change_request(
        subscription_id=subscription_id,
        change_type="decrease",
        quantity_change=quantity_to_decrease,
        requested_by=requested_by,
        prorated_charge=None,  # No charge for decreases
        notes=notes,
    )
    
    logger.info(
        f"Requested subscription decrease: {quantity_to_decrease} licenses from {subscription_id}, "
        f"to be applied at term end"
    )
    
    return {
        "change_request": change_request,
        "preview": preview,
    }


async def cancel_pending_change(
    change_request_id: str,
) -> None:
    """Cancel a pending change request.
    
    Can only cancel pending decrease requests (additions are applied immediately).
    """
    change_request = await change_requests_repo.get_change_request(change_request_id)
    
    if not change_request:
        raise ValueError("Change request not found")
    
    if change_request["status"] != "pending":
        raise ValueError(f"Cannot cancel change request with status: {change_request['status']}")
    
    await change_requests_repo.cancel_change_request(change_request_id)
    
    logger.info(f"Cancelled change request {change_request_id}")


async def process_pending_decreases_for_subscription(
    subscription_id: str,
) -> dict[str, Any]:
    """Process all pending decrease requests for a subscription at term end.
    
    This should be called when a subscription term ends or renews.
    
    Returns:
        Dictionary with:
        - processed_count: Number of decreases processed
        - total_decrease: Total licenses removed
        - final_quantity: Final subscription quantity
    """
    pending_decreases = await change_requests_repo.list_pending_changes_for_subscription(
        subscription_id
    )
    
    # Filter to only decreases
    pending_decreases = [
        change for change in pending_decreases if change["change_type"] == "decrease"
    ]
    
    if not pending_decreases:
        return {
            "processed_count": 0,
            "total_decrease": 0,
            "final_quantity": None,
        }
    
    # Calculate total decrease
    total_decrease = sum(change["quantity_change"] for change in pending_decreases)
    
    # Get current subscription
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    current_quantity = subscription["quantity"]
    new_quantity = max(0, current_quantity - total_decrease)
    
    # Update subscription quantity
    await subscriptions_repo.update_subscription(
        subscription_id,
        quantity=new_quantity,
    )
    
    # Mark all decreases as applied
    for change in pending_decreases:
        await change_requests_repo.apply_change_request(change["id"])
    
    logger.info(
        f"Processed {len(pending_decreases)} pending decreases for subscription {subscription_id}, "
        f"removed {total_decrease} licenses, new quantity: {new_quantity}"
    )
    
    return {
        "processed_count": len(pending_decreases),
        "total_decrease": total_decrease,
        "final_quantity": new_quantity,
    }
