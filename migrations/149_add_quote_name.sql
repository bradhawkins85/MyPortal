-- Add name column to shop_quotes table
ALTER TABLE shop_quotes
  ADD COLUMN IF NOT EXISTS name VARCHAR(255);

-- Update existing quotes with default names based on quote_number
UPDATE shop_quotes
SET name = CONCAT('Quote ', quote_number)
WHERE name IS NULL OR name = '';
