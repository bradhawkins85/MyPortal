-- Replace term_days with commitment_type and payment_frequency
-- Add pricing fields for different commitment/payment combinations

-- Add new columns for commitment type and payment frequency
ALTER TABLE shop_products 
  ADD COLUMN IF NOT EXISTS commitment_type ENUM('monthly', 'annual') NULL,
  ADD COLUMN IF NOT EXISTS payment_frequency ENUM('monthly', 'annual') NULL;

-- Add pricing columns for different commitment/payment combinations
ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS price_monthly_commitment DECIMAL(10,2) NULL,
  ADD COLUMN IF NOT EXISTS price_annual_monthly_payment DECIMAL(10,2) NULL,
  ADD COLUMN IF NOT EXISTS price_annual_annual_payment DECIMAL(10,2) NULL;

-- Migrate existing data: products with subscription_category_id are annual subscriptions
-- The existing 'price' field becomes the price_annual_annual_payment
UPDATE shop_products 
SET 
  commitment_type = 'annual',
  payment_frequency = 'annual',
  price_annual_annual_payment = price
WHERE subscription_category_id IS NOT NULL;

-- Drop the term_days column as it's no longer needed
ALTER TABLE shop_products 
  DROP COLUMN IF EXISTS term_days;

-- Add comment for clarity
ALTER TABLE shop_products 
  COMMENT = 'Products can be subscription-based with monthly/annual commitments and payment frequencies';
