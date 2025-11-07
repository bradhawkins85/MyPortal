-- Create scheduled_invoices table for auto-generated renewal invoices
CREATE TABLE IF NOT EXISTS scheduled_invoices (
  id INT AUTO_INCREMENT PRIMARY KEY,
  customer_id INT NOT NULL,
  scheduled_for_date DATE NOT NULL,
  status ENUM('scheduled', 'issued', 'canceled') NOT NULL DEFAULT 'scheduled',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (customer_id) REFERENCES companies(id) ON DELETE CASCADE,
  UNIQUE KEY unique_customer_scheduled_date (customer_id, scheduled_for_date)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_invoices_customer ON scheduled_invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_invoices_date ON scheduled_invoices(scheduled_for_date);
CREATE INDEX IF NOT EXISTS idx_scheduled_invoices_status ON scheduled_invoices(status);

-- Create scheduled_invoice_lines table for line items on scheduled invoices
CREATE TABLE IF NOT EXISTS scheduled_invoice_lines (
  id INT AUTO_INCREMENT PRIMARY KEY,
  scheduled_invoice_id INT NOT NULL,
  subscription_id CHAR(36) NOT NULL,
  product_id INT NOT NULL,
  term_start DATE NOT NULL,
  term_end DATE NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (scheduled_invoice_id) REFERENCES scheduled_invoices(id) ON DELETE CASCADE,
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES shop_products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scheduled_invoice_lines_invoice ON scheduled_invoice_lines(scheduled_invoice_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_invoice_lines_subscription ON scheduled_invoice_lines(subscription_id);
