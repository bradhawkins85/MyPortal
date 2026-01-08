CREATE TABLE IF NOT EXISTS shop_quotes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  company_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  quote_number VARCHAR(20) NOT NULL,
  status VARCHAR(50) DEFAULT 'active',
  notes TEXT,
  po_number VARCHAR(100),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  FOREIGN KEY (product_id) REFERENCES shop_products(id),
  INDEX idx_quote_number_company (quote_number, company_id),
  INDEX idx_company_expires (company_id, expires_at)
);

ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_access_quotes TINYINT(1);

UPDATE user_companies
SET can_access_quotes = 1
WHERE can_access_quotes IS NULL;

ALTER TABLE user_companies
  MODIFY can_access_quotes TINYINT(1) DEFAULT 0 NOT NULL;
