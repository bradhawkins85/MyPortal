-- Add price change tracking fields to shop_products
ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS scheduled_price DECIMAL(10,2) NULL,
  ADD COLUMN IF NOT EXISTS scheduled_vip_price DECIMAL(10,2) NULL,
  ADD COLUMN IF NOT EXISTS scheduled_buy_price DECIMAL(10,2) NULL,
  ADD COLUMN IF NOT EXISTS price_change_date DATE NULL,
  ADD COLUMN IF NOT EXISTS price_change_notified TINYINT(1) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_products_price_change_date 
  ON shop_products(price_change_date, price_change_notified);
