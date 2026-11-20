-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Lets a one-time purchase product be listed with the subscriptions in a
-- subscription category (e.g. on-demand capacity packs next to the vendor's
-- plans) without making it a subscription. subscription_category_id still
-- means "this product is a subscription"; this column is for grouping only.
ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS display_subscription_category_id INT NULL;

CREATE INDEX IF NOT EXISTS idx_shop_products_display_subscription_category
  ON shop_products (display_subscription_category_id);
