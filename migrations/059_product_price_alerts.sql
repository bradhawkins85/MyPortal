CREATE TABLE IF NOT EXISTS product_price_alerts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  vip_price DECIMAL(10,2) NULL,
  buy_price DECIMAL(10,2) NOT NULL,
  threshold_price DECIMAL(10,2) NOT NULL,
  triggered_at DATETIME NOT NULL,
  emailed_at DATETIME NULL,
  resolved_at DATETIME NULL,
  CONSTRAINT fk_product_price_alerts_product FOREIGN KEY (product_id)
    REFERENCES shop_products(id) ON DELETE CASCADE
);

ALTER TABLE product_price_alerts
  ADD INDEX idx_product_price_alerts_product_resolved (product_id, resolved_at);
