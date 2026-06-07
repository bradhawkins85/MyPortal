CREATE TABLE IF NOT EXISTS shop_freight_rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  priority INT NOT NULL DEFAULT 0,
  is_default TINYINT(1) NOT NULL DEFAULT 0,
  freight_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  conditions JSON NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shop_freight_rules_priority ON shop_freight_rules (priority);
CREATE INDEX IF NOT EXISTS idx_shop_freight_rules_is_active ON shop_freight_rules (is_active);
