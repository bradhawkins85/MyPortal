-- Extend shop_products to support subscription functionality
ALTER TABLE shop_products 
  ADD COLUMN IF NOT EXISTS subscription_category_id INT NULL,
  ADD COLUMN IF NOT EXISTS term_days INT NOT NULL DEFAULT 365;

-- Add foreign key constraint to subscription_categories
ALTER TABLE shop_products 
  ADD CONSTRAINT fk_products_subscription_category 
  FOREIGN KEY (subscription_category_id) 
  REFERENCES subscription_categories(id) 
  ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_products_subscription_category ON shop_products(subscription_category_id);
