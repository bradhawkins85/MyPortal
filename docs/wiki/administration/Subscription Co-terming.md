# Subscription Co-Terming Implementation Guide

## Overview

This document describes the subscription co-terming feature implementation for MyPortal.

## What Was Implemented

### Core Infrastructure (v1)

1. **Database Schema**
   - `subscription_categories`: Groups products that co-term together
   - `subscriptions`: Tracks active customer subscriptions with lifecycle
   - `scheduled_invoices` + `scheduled_invoice_lines`: T-60 renewal invoices
   - Extensions to `shop_products`: `subscription_category_id`, `term_days`
   - Extensions to `shop_cart_items`: Co-term fields (ready for UI)

2. **Repository Layer**
   - `app/repositories/subscription_categories.py`: Category management
   - `app/repositories/subscriptions.py`: Subscription CRUD with filtering
   - `app/repositories/scheduled_invoices.py`: Renewal invoice management

3. **Service Layer**
   - `app/services/subscription_pricing.py`: Proration calculations per spec
   - `app/services/subscription_renewals.py`: T-60 invoice generation
   - `app/services/subscription_shop_integration.py`: Order → Subscription

4. **API & UI**
   - `GET /api/v1/subscriptions`: List/filter subscriptions
   - `GET /api/v1/subscriptions/{id}`: Get subscription details
   - `/admin/subscriptions`: Admin UI page with filters

5. **Scheduler**
   - Nightly job at 02:00 creates renewal invoices
   - 60 days before end_date
   - Distributed locking ensures no duplicates

## How It Works

### Creating Subscriptions

When a product has a `subscription_category_id` set, purchasing it creates a subscription:

1. User adds product to cart
2. User completes checkout
3. `subscription_shop_integration.create_subscriptions_from_order()` runs
4. For each subscription product:
   - Creates subscription record
   - Sets start_date = today
   - Sets end_date = today + term_days (default 365)
   - Status = 'active'
   - auto_renew = true

### Renewal Process

1. **T-60 Job** runs nightly at 02:00
   - Finds all subscriptions ending in 60 days
   - Groups by (customer_id, end_date)
   - Creates one scheduled_invoice per group
   - Adds scheduled_invoice_lines for each subscription
   - Marks subscriptions as 'pending_renewal'

2. **Idempotency**
   - Checks if invoice already exists for (customer_id, end_date)
   - Uses distributed lock to prevent duplicate execution
   - Safe for multiple workers

### Co-Term Pricing Formula

```python
days_inclusive = (end_date - today).days + 1
daily_rate = item_price / 365  # Always 365, even leap years
coterm_price = round(daily_rate * days_inclusive, 2)  # ROUND_HALF_UP
```

## Usage Examples

### Mark a Product as Subscription

```python
from app.repositories import shop as shop_repo

# Set subscription category on a product
await shop_repo.update_product(
    product_id=123,
    subscription_category_id=1,  # e.g., "Security" category
    term_days=365,  # Annual subscription
)
```

### Create a Subscription Category

```python
from app.repositories import subscription_categories as categories_repo

category = await categories_repo.create_category(
    name="Security",
    description="Security and compliance products"
)
```

### Query Subscriptions

```python
from app.repositories import subscriptions as subscriptions_repo

# Get all active subscriptions for a customer
subs = await subscriptions_repo.list_subscriptions(
    customer_id=42,
    status="active"
)

# Get subscriptions ending soon
from datetime import date, timedelta
sixty_days = date.today() + timedelta(days=60)

subs = await subscriptions_repo.list_subscriptions(
    customer_id=42,
    status="active",
    end_before=sixty_days
)
```

### Calculate Co-Term Price

```python
from datetime import date
from decimal import Decimal
from app.services import subscription_pricing

today = date(2025, 11, 7)
end_date = date(2026, 1, 31)  # Existing subscription end date
item_price = Decimal("365.00")

coterm_price = subscription_pricing.calculate_coterm_price(
    item_price, today, end_date
)
# Returns: Decimal("86.00")

# Get explanation for display
explanation = subscription_pricing.get_pricing_explanation(
    item_price, today, end_date, coterm_price
)
# Returns: {
#   "days_inclusive": 86,
#   "formula": "($365.00 ÷ 365) × 86 days = $86.00",
#   ...
# }
```

### Manual Renewal Job Trigger

```python
from datetime import date
from app.services import subscription_renewals

# Run for a specific date
result = await subscription_renewals.create_renewal_invoices_for_date(
    date.today()
)
# Returns: {
#   "processed_count": 5,
#   "invoice_count": 2,
#   "customer_count": 2,
#   "skipped_count": 0
# }
```

## Permissions

Subscription management requires:
- Company admin status
- **AND** License permission
- **AND** Cart permission

This is enforced in:
- API endpoints (`/api/v1/subscriptions`)
- Admin UI page (`/admin/subscriptions`)

## Testing

Run subscription pricing tests:
```bash
pytest tests/test_subscription_pricing.py -v
```

All tests validate:
- Proration formula (÷365, inclusive days)
- Leap year handling
- Rounding behavior
- Edge cases (same-day, past dates, etc.)

## What's NOT Implemented (v1 Scope)

### Cart UI for Co-Term Toggle

The database fields exist but UI is not implemented:
- `shop_cart_items.coterm_enabled`
- `shop_cart_items.coterm_end_date`
- `shop_cart_items.coterm_price`

To implement:
1. Check if product has subscription category
2. Query for active subscriptions in that category
3. Get max(end_date) as anchor
4. Show toggle: "Co-term to {Category} renewal on {date}"
5. When toggled, call pricing API and update cart line

### Features Explicitly Deferred

Per requirements, these are NOT in v1:
- Seat-based licensing
- Tiered pricing
- Usage-based billing
- Monthly billing (annual only)
- Mid-term downgrades/refunds
- Cross-tenant co-terming
- Payment collection changes

## Database Migrations

Migration files in order:
1. `104_subscription_categories.sql`: Create categories table
2. `105_extend_shop_products_for_subscriptions.sql`: Add subscription fields to products
3. `106_subscriptions.sql`: Create subscriptions table
4. `107_scheduled_invoices.sql`: Create renewal invoice tables
5. `108_cart_coterm_fields.sql`: Add co-term fields to cart items

Migrations run automatically on app startup via MyPortal's migration runner.

## Monitoring

### Logs to Watch

```bash
# Renewal job execution
grep "subscription_renewals" app.log

# Subscription creation from orders
grep "Created subscription from order" app.log

# Failures
grep "Failed to create subscription" app.log
```

### Key Metrics

- Subscription count by status
- Renewal invoice creation success rate
- Co-term toggle usage (when UI implemented)
- Average proration revenue

## Troubleshooting

### Renewal Job Not Running

Check:
1. Scheduler service is started: `grep "Scheduler started" app.log`
2. Job is registered: `grep "subscription-renewals" app.log`
3. No lock contention: `grep "already running" app.log`

### Subscriptions Not Created

Check:
1. Product has `subscription_category_id` set
2. Order completed successfully
3. Logs for subscription creation errors

### Wrong Proration Price

Verify:
1. Calculation uses 365 days (not 366 in leap years)
2. Days are inclusive (end - start + 1)
3. Rounding is ROUND_HALF_UP to 2 decimals

## Future Enhancements

To add co-term UI:
1. Detect eligible products in cart
2. Fetch anchor end_date for category
3. Add toggle control to cart line
4. Call pricing calculation on toggle
5. Store coterm_enabled, coterm_end_date, coterm_price in cart
6. Use coterm values when creating subscription

To add mid-term changes:
1. Create subscription modification endpoints
2. Calculate prorated refunds/charges
3. Create adjustment invoices
4. Update subscription end_date and pricing

## Security Considerations

- All SQL queries use parameterized statements
- Permission checks on all endpoints
- Distributed locking prevents race conditions
- Input validation on all user data
- No secrets in repository

## Support

For questions or issues:
1. Check logs for error messages
2. Verify database schema is up to date
3. Ensure permissions are correctly configured
4. Review this documentation
