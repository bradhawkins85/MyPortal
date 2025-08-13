CREATE TABLE IF NOT EXISTS shop_categories (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL UNIQUE
);

ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS category_id INT NULL;
ALTER TABLE shop_products ADD CONSTRAINT fk_shop_products_category FOREIGN KEY (category_id) REFERENCES shop_categories(id) ON DELETE SET NULL;
