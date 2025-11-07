"""Service for managing subscription renewals and scheduled invoices."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger

from app.repositories import subscriptions as subscriptions_repo
from app.repositories import scheduled_invoices as invoices_repo
from app.repositories import shop as shop_repo


async def create_renewal_invoices_for_date(target_date: date) -> dict[str, Any]:
    """Create scheduled invoices for subscriptions ending 60 days from target_date.
    
    This is the nightly job that:
    1. Finds all active subscriptions ending on target_date + 60 days
    2. Groups them by (customer_id, end_date)
    3. Creates one scheduled invoice per group (idempotent)
    4. Marks subscriptions as pending_renewal
    
    Args:
        target_date: The date to run the job for (typically today)
        
    Returns:
        Summary dict with:
        - processed_count: Number of subscriptions processed
        - invoice_count: Number of invoices created
        - customer_count: Number of unique customers
        - skipped_count: Number of invoices that already existed
    """
    # Calculate the renewal date (60 days from target)
    renewal_date = target_date + timedelta(days=60)
    
    logger.info(
        "Starting renewal invoice creation",
        target_date=target_date,
        renewal_date=renewal_date,
    )
    
    # Find all active subscriptions ending on renewal_date
    subscriptions = await subscriptions_repo.list_subscriptions(
        status="active",
        end_before=renewal_date + timedelta(days=1),  # end_date <= renewal_date
        end_after=renewal_date - timedelta(days=1),   # end_date >= renewal_date
    )
    
    # Filter to exact match (since we use < and > in query)
    subscriptions = [
        sub for sub in subscriptions if sub["end_date"] == renewal_date
    ]
    
    if not subscriptions:
        logger.info("No subscriptions due for renewal", renewal_date=renewal_date)
        return {
            "processed_count": 0,
            "invoice_count": 0,
            "customer_count": 0,
            "skipped_count": 0,
        }
    
    # Group subscriptions by (customer_id, end_date)
    grouped: dict[tuple[int, date], list[dict[str, Any]]] = defaultdict(list)
    for sub in subscriptions:
        key = (sub["customer_id"], sub["end_date"])
        grouped[key].append(sub)
    
    invoice_count = 0
    skipped_count = 0
    processed_sub_ids: list[str] = []
    
    for (customer_id, end_date), group_subs in grouped.items():
        # Check if invoice already exists (idempotency)
        existing = await invoices_repo.get_scheduled_invoice_by_customer_and_date(
            customer_id, end_date
        )
        
        if existing:
            logger.debug(
                "Scheduled invoice already exists",
                customer_id=customer_id,
                end_date=end_date,
                invoice_id=existing["id"],
            )
            skipped_count += 1
            
            # Still mark subscriptions as pending_renewal if they aren't already
            for sub in group_subs:
                if sub["status"] == "active":
                    await subscriptions_repo.update_subscription(
                        sub["id"], status="pending_renewal"
                    )
                    processed_sub_ids.append(sub["id"])
            continue
        
        # Create the scheduled invoice
        invoice = await invoices_repo.create_scheduled_invoice(
            customer_id=customer_id,
            scheduled_for_date=end_date,
            status="scheduled",
        )
        
        invoice_count += 1
        logger.info(
            "Created scheduled invoice",
            invoice_id=invoice["id"],
            customer_id=customer_id,
            end_date=end_date,
            subscription_count=len(group_subs),
        )
        
        # Add line items for each subscription in the group
        for sub in group_subs:
            # Calculate next term dates
            next_term_start = sub["end_date"] + timedelta(days=1)
            
            # Fetch product to determine commitment type and term length
            product = await shop_repo.get_product_by_id(sub["product_id"])
            
            # Determine term length based on commitment type
            if product and product.get("commitment_type"):
                commitment_type = product.get("commitment_type")
                if commitment_type == "monthly":
                    term_days = 30  # Monthly commitment = 30 days
                else:  # annual
                    term_days = 365  # Annual commitment = 365 days
            else:
                # Fallback for legacy subscriptions
                term_days = 365
                
            next_term_end = next_term_start + timedelta(days=term_days - 1)
            
            await invoices_repo.add_invoice_line(
                invoice_id=invoice["id"],
                subscription_id=sub["id"],
                product_id=sub["product_id"],
                term_start=next_term_start,
                term_end=next_term_end,
                price=sub["unit_price"],  # Full term price for renewal
            )
            
            # Mark subscription as pending_renewal
            await subscriptions_repo.update_subscription(
                sub["id"], status="pending_renewal"
            )
            processed_sub_ids.append(sub["id"])
    
    customer_count = len(grouped)
    
    logger.info(
        "Renewal invoice creation completed",
        target_date=target_date,
        processed_count=len(processed_sub_ids),
        invoice_count=invoice_count,
        customer_count=customer_count,
        skipped_count=skipped_count,
    )
    
    return {
        "processed_count": len(processed_sub_ids),
        "invoice_count": invoice_count,
        "customer_count": customer_count,
        "skipped_count": skipped_count,
        "processed_subscription_ids": processed_sub_ids,
    }


async def get_next_scheduled_invoice_for_subscription(
    subscription_id: str,
) -> dict[str, Any] | None:
    """Get the next scheduled invoice for a subscription."""
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        return None
    
    # Look for scheduled invoices for this customer on the subscription's end_date
    invoice = await invoices_repo.get_scheduled_invoice_by_customer_and_date(
        subscription["customer_id"],
        subscription["end_date"],
    )
    
    return invoice
