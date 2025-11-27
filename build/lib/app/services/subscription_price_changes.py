"""Service for managing subscription price change notifications."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core.database import db
from app.repositories import billing_contacts as billing_contacts_repo
from app.repositories import subscriptions as subscriptions_repo
from app.services import email as email_service


async def get_products_with_pending_price_changes() -> list[dict[str, Any]]:
    """Get products that have price changes scheduled for today or earlier that haven't been notified."""
    rows = await db.fetch_all(
        """
        SELECT 
            p.id,
            p.name,
            p.sku,
            p.subscription_category_id,
            p.price AS current_price,
            p.vip_price AS current_vip_price,
            p.buy_price AS current_buy_price,
            p.scheduled_price,
            p.scheduled_vip_price,
            p.scheduled_buy_price,
            p.price_change_date,
            sc.name AS category_name
        FROM shop_products p
        LEFT JOIN subscription_categories sc ON sc.id = p.subscription_category_id
        WHERE p.price_change_date IS NOT NULL
          AND p.price_change_date <= CURDATE()
          AND p.price_change_notified = 0
          AND p.subscription_category_id IS NOT NULL
        ORDER BY p.subscription_category_id, p.name
        """
    )
    
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "sku": str(row["sku"]) if row["sku"] else "",
            "subscription_category_id": int(row["subscription_category_id"]) if row["subscription_category_id"] else None,
            "category_name": str(row["category_name"]) if row["category_name"] else "Uncategorized",
            "current_price": Decimal(str(row["current_price"])) if row["current_price"] else Decimal("0"),
            "current_vip_price": Decimal(str(row["current_vip_price"])) if row["current_vip_price"] else None,
            "current_buy_price": Decimal(str(row["current_buy_price"])) if row["current_buy_price"] else Decimal("0"),
            "scheduled_price": Decimal(str(row["scheduled_price"])) if row["scheduled_price"] else None,
            "scheduled_vip_price": Decimal(str(row["scheduled_vip_price"])) if row["scheduled_vip_price"] else None,
            "scheduled_buy_price": Decimal(str(row["scheduled_buy_price"])) if row["scheduled_buy_price"] else None,
            "price_change_date": row["price_change_date"],
        }
        for row in rows
    ]


async def get_companies_with_subscriptions_for_products(
    product_ids: list[int],
) -> dict[int, list[int]]:
    """Get companies that have active subscriptions for the given products.
    
    Returns a dict mapping product_id to list of company_ids.
    """
    if not product_ids:
        return {}
    
    placeholders = ", ".join(["%s"] * len(product_ids))
    rows = await db.fetch_all(
        f"""
        SELECT DISTINCT product_id, customer_id
        FROM subscriptions
        WHERE product_id IN ({placeholders})
          AND status = 'active'
        """,
        tuple(product_ids),
    )
    
    result: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        product_id = int(row["product_id"])
        customer_id = int(row["customer_id"])
        if customer_id not in result[product_id]:
            result[product_id].append(customer_id)
    
    return dict(result)


def _format_price(price: Decimal | None) -> str:
    """Format a price for display."""
    if price is None:
        return "N/A"
    return f"${price:.2f}"


def _build_price_change_email_html(
    category_name: str,
    products: list[dict[str, Any]],
    effective_date: date,
) -> str:
    """Build HTML email body for price change notification."""
    product_rows = []
    for product in products:
        old_price = _format_price(product.get("current_price"))
        new_price = _format_price(product.get("scheduled_price"))
        
        # Check if VIP price is changing
        vip_info = ""
        if product.get("scheduled_vip_price") is not None:
            old_vip = _format_price(product.get("current_vip_price"))
            new_vip = _format_price(product.get("scheduled_vip_price"))
            vip_info = f" (VIP: {old_vip} → {new_vip})"
        
        product_rows.append(
            f"""
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{product["name"]}</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{old_price}{vip_info}</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{new_price}{vip_info}</td>
            </tr>
            """
        )
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #374151; background-color: #f9fafb; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden;">
            <div style="background-color: #3b82f6; color: white; padding: 24px; text-align: center;">
                <h1 style="margin: 0; font-size: 24px; font-weight: 600;">Subscription Price Change Notice</h1>
            </div>
            <div style="padding: 32px 24px;">
                <p style="margin: 0 0 16px 0; font-size: 16px;">
                    This is to inform you of upcoming price changes for your <strong>{category_name}</strong> subscriptions.
                </p>
                <p style="margin: 0 0 24px 0; font-size: 16px;">
                    <strong>Effective Date:</strong> {effective_date.strftime("%B %d, %Y")}
                </p>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
                    <thead>
                        <tr style="background-color: #f3f4f6;">
                            <th style="padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #d1d5db;">Product</th>
                            <th style="padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #d1d5db;">Current Price</th>
                            <th style="padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #d1d5db;">New Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(product_rows)}
                    </tbody>
                </table>
                <p style="margin: 0 0 16px 0; font-size: 14px; color: #6b7280;">
                    These new prices will be reflected in your next billing cycle on or after the effective date.
                    If you have any questions about these changes, please contact our support team.
                </p>
            </div>
            <div style="background-color: #f3f4f6; padding: 16px 24px; text-align: center; font-size: 12px; color: #6b7280;">
                <p style="margin: 0;">This is an automated notification. Please do not reply to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """


def _build_price_change_email_text(
    category_name: str,
    products: list[dict[str, Any]],
    effective_date: date,
) -> str:
    """Build plain text email body for price change notification."""
    product_lines = []
    for product in products:
        old_price = _format_price(product.get("current_price"))
        new_price = _format_price(product.get("scheduled_price"))
        
        vip_info = ""
        if product.get("scheduled_vip_price") is not None:
            old_vip = _format_price(product.get("current_vip_price"))
            new_vip = _format_price(product.get("scheduled_vip_price"))
            vip_info = f" (VIP: {old_vip} -> {new_vip})"
        
        product_lines.append(f"  - {product['name']}: {old_price} -> {new_price}{vip_info}")
    
    return f"""
Subscription Price Change Notice

This is to inform you of upcoming price changes for your {category_name} subscriptions.

Effective Date: {effective_date.strftime("%B %d, %Y")}

Price Changes:
{"\\n".join(product_lines)}

These new prices will be reflected in your next billing cycle on or after the effective date.
If you have any questions about these changes, please contact our support team.

---
This is an automated notification. Please do not reply to this email.
    """.strip()


async def send_price_change_notifications() -> dict[str, Any]:
    """Send price change notifications to billing contacts.
    
    Groups products by subscription category and company, then sends
    one email per category to each company's billing contacts.
    
    Returns a summary of notifications sent.
    """
    products = await get_products_with_pending_price_changes()
    
    if not products:
        logger.info("No products with pending price changes found")
        return {
            "products_processed": 0,
            "emails_sent": 0,
            "companies_notified": 0,
        }
    
    # Get companies that have subscriptions for these products
    product_ids = [p["id"] for p in products]
    product_company_map = await get_companies_with_subscriptions_for_products(product_ids)
    
    # Collect all unique company IDs
    company_ids: set[int] = set()
    for companies in product_company_map.values():
        company_ids.update(companies)
    
    if not company_ids:
        logger.info("No companies with active subscriptions found for products with price changes")
        # Still mark products as notified since there are no customers to notify
        for product in products:
            await mark_product_price_change_notified(product["id"])
        return {
            "products_processed": len(products),
            "emails_sent": 0,
            "companies_notified": 0,
        }
    
    # Get billing contacts for all companies
    billing_contacts = await billing_contacts_repo.get_billing_contacts_for_companies(list(company_ids))
    
    # Group products by category and then by company
    # Structure: {category_id: {company_id: [products]}}
    category_company_products: dict[int | None, dict[int, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    
    for product in products:
        category_id = product["subscription_category_id"]
        # Find companies with subscriptions for this product
        for company_id in product_company_map.get(product["id"], []):
            category_company_products[category_id][company_id].append(product)
    
    emails_sent = 0
    companies_notified: set[int] = set()
    notified_products: set[int] = set()
    
    # Send one email per category per company
    for category_id, company_products in category_company_products.items():
        for company_id, products_for_company in company_products.items():
            contacts = billing_contacts.get(company_id, [])
            
            if not contacts:
                logger.warning(
                    f"No billing contacts found for company {company_id}, skipping notification"
                )
                continue
            
            # Get category name and effective date from first product
            category_name = products_for_company[0]["category_name"]
            effective_date = products_for_company[0]["price_change_date"]
            
            # Build email
            recipients = [contact["email"] for contact in contacts]
            subject = f"Price Change Notice: {category_name} Subscriptions"
            html_body = _build_price_change_email_html(
                category_name, products_for_company, effective_date
            )
            text_body = _build_price_change_email_text(
                category_name, products_for_company, effective_date
            )
            
            # Send email
            success, _ = await email_service.send_email(
                subject=subject,
                recipients=recipients,
                html_body=html_body,
                text_body=text_body,
            )
            
            if success:
                emails_sent += 1
                companies_notified.add(company_id)
                for product in products_for_company:
                    notified_products.add(product["id"])
                logger.info(
                    f"Sent price change notification for {category_name} to company {company_id}"
                )
            else:
                logger.error(
                    f"Failed to send price change notification for {category_name} to company {company_id}"
                )
    
    # Mark all processed products as notified (even if some emails failed)
    # This prevents repeated attempts for products that have no billing contacts
    for product in products:
        await mark_product_price_change_notified(product["id"])
    
    return {
        "products_processed": len(products),
        "emails_sent": emails_sent,
        "companies_notified": len(companies_notified),
    }


async def mark_product_price_change_notified(product_id: int) -> None:
    """Mark a product's price change as notified."""
    await db.execute(
        "UPDATE shop_products SET price_change_notified = 1 WHERE id = %s",
        (product_id,),
    )


async def apply_scheduled_price_changes() -> dict[str, Any]:
    """Apply scheduled price changes that are due.
    
    Updates products where the price_change_date is today or earlier
    and the notified flag is set.
    
    Returns a summary of products updated.
    """
    # Get products ready for price change
    rows = await db.fetch_all(
        """
        SELECT 
            id,
            name,
            price AS current_price,
            scheduled_price,
            vip_price AS current_vip_price,
            scheduled_vip_price,
            buy_price AS current_buy_price,
            scheduled_buy_price,
            price_change_date
        FROM shop_products
        WHERE price_change_date IS NOT NULL
          AND price_change_date <= CURDATE()
          AND price_change_notified = 1
        """
    )
    
    if not rows:
        logger.info("No scheduled price changes to apply")
        return {"products_updated": 0}
    
    products_updated = 0
    
    for row in rows:
        product_id = int(row["id"])
        product_name = str(row["name"])
        
        # Update the price fields
        await db.execute(
            """
            UPDATE shop_products
            SET 
                price = COALESCE(scheduled_price, price),
                vip_price = COALESCE(scheduled_vip_price, vip_price),
                buy_price = COALESCE(scheduled_buy_price, buy_price),
                scheduled_price = NULL,
                scheduled_vip_price = NULL,
                scheduled_buy_price = NULL,
                price_change_date = NULL,
                price_change_notified = 0
            WHERE id = %s
            """,
            (product_id,),
        )
        
        products_updated += 1
        logger.info(f"Applied scheduled price change for product {product_id}: {product_name}")
    
    return {"products_updated": products_updated}
