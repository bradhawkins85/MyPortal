CREATE TABLE IF NOT EXISTS shop_cart_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  session_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  product_name VARCHAR(255) NOT NULL,
  product_sku VARCHAR(100) NOT NULL,
  product_vendor_sku VARCHAR(100) NULL,
  product_description TEXT NULL,
  product_image_url TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_shop_cart_session FOREIGN KEY (session_id) REFERENCES user_sessions(id) ON DELETE CASCADE,
  CONSTRAINT fk_shop_cart_product FOREIGN KEY (product_id) REFERENCES shop_products(id),
  UNIQUE KEY uq_shop_cart_session_product (session_id, product_id)
);
