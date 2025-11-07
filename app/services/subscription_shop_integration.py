"""Service for integrating subscriptions with the shop/cart system."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from loguru import logger

from app.repositories import shop as shop_repo
from app.repositories import subscriptions as subscriptions_repo
from app.services import subscription_pricing


async def create_subscriptions_from_order(
    *,
    order_number: str,
    company_id: int,
    user_id: int,
) -> list[dict[str, Any]]:
    """Create subscriptions for all eligible products in an order.
    
    This should be called after an order is successfully placed.
    
    Args:
        order_number: The order number
        company_id: The customer company ID
        user_id: The user who placed the order
        
    Returns:
        List of created subscriptions
    """
    # Get order items
    order_items = await shop_repo.list_order_items(order_number, company_id)
    if not order_items:
        logger.warning("No order items found", order_number=order_number)
        return []
    
    # Get product IDs from order items
    product_ids = [int(item["product_id"]) for item in order_items]
    
    # Get product details to check which ones are subscription products
    products = await shop_repo.list_products_by_ids(product_ids, company_id=company_id)
    product_lookup: dict[int, dict[str, Any]] = {
        int(p["id"]): p for p in products if p.get("id")
    }
    
    created_subscriptions: list[dict[str, Any]] = []
    today = date.today()
    
    for item in order_items:
        product_id = int(item["product_id"])
        product = product_lookup.get(product_id)
        
        if not product:
            logger.warning(
                "Product not found for order item",
                product_id=product_id,
                order_number=order_number,
            )
            continue
        
        # Check if this is a subscription product
        subscription_category_id = product.get("subscription_category_id")
        if subscription_category_id is None:
            # Not a subscription product, skip
            continue
        
        # Get cart metadata if available (co-term settings)
        # For v1, we'll create full-term subscriptions
        # In a full implementation, we'd check cart_repo for coterm settings
        
        quantity = int(item.get("quantity", 1))
        unit_price = Decimal(str(item.get("price", 0)))
        term_days = int(product.get("term_days", 365))
        
        # Calculate subscription dates
        start_date = today
        end_date = subscription_pricing.calculate_full_term_end_date(
            start_date, term_days
        )
        
        # Create the subscription
        try:
            subscription = await subscriptions_repo.create_subscription(
                customer_id=company_id,
                product_id=product_id,
                subscription_category_id=subscription_category_id,
                start_date=start_date,
                end_date=end_date,
                quantity=quantity,
                unit_price=unit_price,
                prorated_price=None,  # Full term in v1
                status="active",
                auto_renew=True,
                created_by=user_id,
            )
            
            created_subscriptions.append(subscription)
            
            logger.info(
                "Created subscription from order",
                subscription_id=subscription["id"],
                order_number=order_number,
                product_id=product_id,
                company_id=company_id,
                start_date=start_date,
                end_date=end_date,
            )
        
        except Exception as exc:
            logger.error(
                "Failed to create subscription from order",
                order_number=order_number,
                product_id=product_id,
                error=str(exc),
            )
            # Don't fail the whole order, continue with other items
            continue
    
    if created_subscriptions:
        logger.info(
            "Created subscriptions from order",
            order_number=order_number,
            count=len(created_subscriptions),
        )
    
    return created_subscriptions
