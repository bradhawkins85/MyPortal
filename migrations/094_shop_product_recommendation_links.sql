ALTER TABLE shop_products
  DROP FOREIGN KEY fk_shop_products_cross_sell,
  DROP FOREIGN KEY fk_shop_products_upsell;

CREATE TABLE IF NOT EXISTS shop_product_cross_sells (
  product_id INT NOT NULL,
  related_product_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, related_product_id),
  CONSTRAINT fk_shop_product_cross_sells_product
    FOREIGN KEY (product_id) REFERENCES shop_products(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_shop_product_cross_sells_related
    FOREIGN KEY (related_product_id) REFERENCES shop_products(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shop_product_upsells (
  product_id INT NOT NULL,
  related_product_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, related_product_id),
  CONSTRAINT fk_shop_product_upsells_product
    FOREIGN KEY (product_id) REFERENCES shop_products(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_shop_product_upsells_related
    FOREIGN KEY (related_product_id) REFERENCES shop_products(id)
    ON DELETE CASCADE
);

INSERT INTO shop_product_cross_sells (product_id, related_product_id)
SELECT id AS product_id, cross_sell_product_id AS related_product_id
FROM shop_products
WHERE cross_sell_product_id IS NOT NULL AND cross_sell_product_id <> id
ON DUPLICATE KEY UPDATE related_product_id = VALUES(related_product_id);

INSERT INTO shop_product_upsells (product_id, related_product_id)
SELECT id AS product_id, upsell_product_id AS related_product_id
FROM shop_products
WHERE upsell_product_id IS NOT NULL AND upsell_product_id <> id
ON DUPLICATE KEY UPDATE related_product_id = VALUES(related_product_id);

ALTER TABLE shop_products
  DROP COLUMN cross_sell_product_id,
  DROP COLUMN upsell_product_id;
