-- BC11: Add vendors table for business continuity plans
-- Vendors can be service providers, suppliers, or other external parties
-- with SLA information and contact details

CREATE TABLE IF NOT EXISTS bc_vendor (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  vendor_type VARCHAR(100) COMMENT 'e.g., IT Service Provider, Supplier, Cloud Provider, etc.',
  contact_name VARCHAR(255),
  contact_email VARCHAR(255),
  contact_phone VARCHAR(50),
  sla_notes TEXT COMMENT 'Service Level Agreement details and notes',
  contract_reference VARCHAR(255) COMMENT 'Contract number or reference',
  criticality VARCHAR(50) COMMENT 'e.g., critical, high, medium, low',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_vendor_plan (plan_id),
  INDEX idx_bc_vendor_criticality (criticality)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
