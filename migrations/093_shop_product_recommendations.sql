ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS cross_sell_product_id INT NULL AFTER category_id,
  ADD COLUMN IF NOT EXISTS upsell_product_id INT NULL AFTER cross_sell_product_id,
  ADD CONSTRAINT fk_shop_products_cross_sell
    FOREIGN KEY (cross_sell_product_id) REFERENCES shop_products(id)
    ON DELETE SET NULL,
  ADD CONSTRAINT fk_shop_products_upsell
    FOREIGN KEY (upsell_product_id) REFERENCES shop_products(id)
    ON DELETE SET NULL;
