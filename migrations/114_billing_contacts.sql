-- Create billing contacts table to track users who should receive billing notifications
CREATE TABLE IF NOT EXISTS billing_contacts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  user_id INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE KEY unique_company_user (company_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_billing_contacts_company ON billing_contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_billing_contacts_user ON billing_contacts(user_id);
