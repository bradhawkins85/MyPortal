CREATE TABLE IF NOT EXISTS shop_products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  price DECIMAL(10,2) NOT NULL,
  stock INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shop_orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  company_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (product_id) REFERENCES shop_products(id)
);

ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_access_shop TINYINT(1);

UPDATE user_companies
SET can_access_shop = 1
WHERE can_access_shop IS NULL;

ALTER TABLE user_companies
  MODIFY can_access_shop TINYINT(1) DEFAULT 0 NOT NULL;
