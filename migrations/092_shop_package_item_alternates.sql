CREATE TABLE IF NOT EXISTS shop_package_item_alternates (
  id INT AUTO_INCREMENT PRIMARY KEY,
  package_item_id INT NOT NULL,
  alternate_product_id INT NOT NULL,
  priority INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_shop_package_item_alternate_item FOREIGN KEY (package_item_id) REFERENCES shop_package_items(id) ON DELETE CASCADE,
  CONSTRAINT fk_shop_package_item_alternate_product FOREIGN KEY (alternate_product_id) REFERENCES shop_products(id),
  UNIQUE KEY uq_shop_package_item_alternate (package_item_id, alternate_product_id),
  KEY idx_shop_package_item_alternate_priority (package_item_id, priority)
);
