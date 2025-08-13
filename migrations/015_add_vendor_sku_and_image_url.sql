ALTER TABLE shop_products ADD COLUMN vendor_sku VARCHAR(255) NOT NULL AFTER sku;
ALTER TABLE shop_products ADD COLUMN image_url VARCHAR(255) AFTER description;
