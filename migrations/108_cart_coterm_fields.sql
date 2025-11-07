-- Extend cart items to support co-term toggles and end dates
ALTER TABLE shop_cart_items 
  ADD COLUMN IF NOT EXISTS coterm_enabled TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS coterm_end_date DATE NULL,
  ADD COLUMN IF NOT EXISTS coterm_price DECIMAL(10,2) NULL;

CREATE INDEX IF NOT EXISTS idx_cart_items_coterm ON shop_cart_items(coterm_enabled);
