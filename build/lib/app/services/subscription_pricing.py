"""Service for calculating subscription pricing with co-terming support."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.repositories import subscriptions as subscriptions_repo


def calculate_coterm_price(
    item_price: Decimal,
    today: date,
    end_date: date,
) -> Decimal:
    """Calculate prorated price for a co-termed subscription.
    
    Formula:
    - days_inclusive = DATEDIFF(end_date, today) + 1
    - daily_rate = item_price / 365
    - coterm_price = round_currency(daily_rate * days_inclusive)
    
    Always divides by 365 (even in leap years) as specified in requirements.
    Uses ROUND_HALF_UP rounding at currency precision (2 decimals).
    
    Args:
        item_price: Base price of the item for full term
        today: Current date (purchase date)
        end_date: Co-term anchor end date
        
    Returns:
        Prorated price rounded to 2 decimal places
        
    Raises:
        ValueError: If end_date is before today
    """
    if end_date < today:
        raise ValueError("End date must be on or after today")
    
    # Calculate inclusive days between today and end_date
    days_inclusive = (end_date - today).days + 1
    
    # Calculate daily rate (always divide by 365)
    daily_rate = item_price / Decimal("365")
    
    # Calculate prorated price
    coterm_price = daily_rate * Decimal(str(days_inclusive))
    
    # Round to currency precision (2 decimals)
    rounded_price = coterm_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    return rounded_price


async def get_coterm_anchor_for_product(
    customer_id: int,
    product_id: int,
    category_id: int | None,
) -> dict[str, Any] | None:
    """Get the co-term anchor (latest end_date) for a product's category.
    
    Returns a dict with:
    - anchor_date: The latest end_date in the category
    - category_id: The category ID used
    - category_name: The category name
    
    Returns None if:
    - Product has no subscription category
    - Customer has no active subscriptions in that category
    """
    if category_id is None:
        return None
    
    anchor_date = await subscriptions_repo.get_category_anchor_end_date(
        customer_id, category_id
    )
    
    if anchor_date is None:
        return None
    
    # Get category info
    from app.repositories import subscription_categories as categories_repo
    category = await categories_repo.get_category(category_id)
    
    return {
        "anchor_date": anchor_date,
        "category_id": category_id,
        "category_name": category["name"] if category else None,
    }


def calculate_full_term_end_date(start_date: date, term_days: int = 365) -> date:
    """Calculate the end date for a full-term subscription.
    
    Args:
        start_date: Subscription start date
        term_days: Length of term in days (default 365)
        
    Returns:
        End date for the subscription
    """
    return start_date + timedelta(days=term_days)


def get_pricing_explanation(
    item_price: Decimal,
    today: date,
    end_date: date,
    coterm_price: Decimal,
) -> dict[str, Any]:
    """Generate a human-readable explanation of the proration calculation.
    
    Returns a dict with:
    - days_inclusive: Number of days in the prorated term
    - daily_rate: Price per day
    - daily_rate_formatted: Formatted daily rate string
    - item_price: Original full-term price
    - item_price_formatted: Formatted item price string
    - coterm_price: Calculated prorated price
    - coterm_price_formatted: Formatted coterm price string
    - formula: Human-readable formula string
    - end_date: The co-term end date
    """
    days_inclusive = (end_date - today).days + 1
    daily_rate = item_price / Decimal("365")
    
    return {
        "days_inclusive": days_inclusive,
        "daily_rate": daily_rate,
        "daily_rate_formatted": f"${daily_rate:.4f}",
        "item_price": item_price,
        "item_price_formatted": f"${item_price:.2f}",
        "coterm_price": coterm_price,
        "coterm_price_formatted": f"${coterm_price:.2f}",
        "formula": f"(${item_price:.2f} ÷ 365) × {days_inclusive} days = ${coterm_price:.2f}",
        "end_date": end_date,
    }
