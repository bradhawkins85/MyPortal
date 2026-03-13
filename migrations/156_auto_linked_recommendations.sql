ALTER TABLE shop_product_cross_sells
  ADD COLUMN IF NOT EXISTS is_auto_linked TINYINT(1) NOT NULL DEFAULT 0;

ALTER TABLE shop_product_upsells
  ADD COLUMN IF NOT EXISTS is_auto_linked TINYINT(1) NOT NULL DEFAULT 0;
