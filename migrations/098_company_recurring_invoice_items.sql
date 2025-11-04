CREATE TABLE IF NOT EXISTS company_recurring_invoice_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  product_code VARCHAR(255) NOT NULL,
  description_template TEXT NOT NULL,
  qty_expression VARCHAR(255) NOT NULL,
  price_override DECIMAL(10, 2) NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  INDEX idx_company_id (company_id),
  INDEX idx_active (active)
);
