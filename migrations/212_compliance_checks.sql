-- Customer Compliance Checks Module
-- Centralised library of compliance checks assignable to customers,
-- with review scheduling, evidence tracking, and full audit trail.

-- Categories for grouping checks (GMP, GLP, Custom, etc.)
CREATE TABLE IF NOT EXISTS compliance_check_categories (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(50) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  is_system TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY unique_category_code (code)
);

-- Seed built-in categories (idempotent)
INSERT IGNORE INTO compliance_check_categories (code, name, description, is_system) VALUES
  ('GMP', 'Good Manufacturing Practices', 'GMP compliance checks for manufacturing environments.', 1),
  ('GLP', 'Good Laboratory Practices', 'GLP compliance checks for laboratory environments.', 1),
  ('CUSTOM', 'Custom', 'User-defined compliance checks.', 0);

-- Central library of compliance checks
CREATE TABLE IF NOT EXISTS compliance_checks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  category_id INT NOT NULL,
  code VARCHAR(100) NOT NULL,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  guidance TEXT,
  default_review_interval_days INT NOT NULL DEFAULT 365,
  default_evidence_required TINYINT(1) NOT NULL DEFAULT 0,
  is_predefined TINYINT(1) NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  sort_order INT NOT NULL DEFAULT 0,
  created_by INT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY unique_check_code (code),
  FOREIGN KEY (category_id) REFERENCES compliance_check_categories(id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_category_id (category_id),
  INDEX idx_is_active (is_active)
);

-- Per-customer assignment of a compliance check
CREATE TABLE IF NOT EXISTS company_compliance_check_assignments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  check_id INT NOT NULL,
  status ENUM('not_started','in_progress','compliant','non_compliant','not_applicable') NOT NULL DEFAULT 'not_started',
  review_interval_days INT,
  last_checked_at DATETIME,
  last_checked_by INT,
  next_review_at DATETIME,
  notes TEXT,
  evidence_summary TEXT,
  owner_user_id INT,
  archived TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (check_id) REFERENCES compliance_checks(id) ON DELETE CASCADE,
  FOREIGN KEY (last_checked_by) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE KEY unique_company_check (company_id, check_id),
  INDEX idx_company_id (company_id),
  INDEX idx_status (status),
  INDEX idx_next_review_at (next_review_at),
  INDEX idx_archived (archived)
);

-- Evidence items attached to an assignment
CREATE TABLE IF NOT EXISTS company_compliance_check_evidence (
  id INT AUTO_INCREMENT PRIMARY KEY,
  assignment_id INT NOT NULL,
  evidence_type ENUM('text','url','file') NOT NULL DEFAULT 'text',
  title VARCHAR(255) NOT NULL,
  content TEXT,
  file_path VARCHAR(500),
  uploaded_by INT,
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (assignment_id) REFERENCES company_compliance_check_assignments(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_assignment_id (assignment_id)
);

-- Immutable audit log for assignment changes
CREATE TABLE IF NOT EXISTS company_compliance_check_audit (
  id INT AUTO_INCREMENT PRIMARY KEY,
  assignment_id INT NOT NULL,
  company_id INT NOT NULL,
  user_id INT,
  action VARCHAR(100) NOT NULL,
  old_status ENUM('not_started','in_progress','compliant','non_compliant','not_applicable'),
  new_status ENUM('not_started','in_progress','compliant','non_compliant','not_applicable'),
  old_last_checked_at DATETIME,
  new_last_checked_at DATETIME,
  change_summary TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (assignment_id) REFERENCES company_compliance_check_assignments(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_assignment_id (assignment_id),
  INDEX idx_company_id (company_id),
  INDEX idx_created_at (created_at)
);
