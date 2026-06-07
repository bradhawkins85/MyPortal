ALTER TABLE shop_freight_rules
  ADD COLUMN IF NOT EXISTS stop_processing TINYINT(1) NOT NULL DEFAULT 0 AFTER is_default;
