CREATE TABLE IF NOT EXISTS shop_product_features (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  feature_name VARCHAR(255) NOT NULL,
  feature_value TEXT NULL,
  position INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_shop_product_features_product FOREIGN KEY (product_id)
    REFERENCES shop_products(id) ON DELETE CASCADE
);

CREATE INDEX idx_shop_product_features_product
  ON shop_product_features(product_id, position);
