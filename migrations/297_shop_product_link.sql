ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS product_link VARCHAR(2048) NULL AFTER image_url;

ALTER TABLE stock_feed
  ADD COLUMN IF NOT EXISTS website_url VARCHAR(2048) NULL AFTER image_url;
