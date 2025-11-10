-- Business Continuity Plans table for DR/IR/BC plans
CREATE TABLE IF NOT EXISTS business_continuity_plans (
  id INT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  plan_type ENUM('disaster_recovery', 'incident_response', 'business_continuity') NOT NULL,
  content LONGTEXT NOT NULL,
  version VARCHAR(50) DEFAULT '1.0',
  status ENUM('draft', 'active', 'archived') DEFAULT 'draft',
  created_by INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  last_reviewed_at DATETIME,
  last_reviewed_by INT,
  FOREIGN KEY (created_by) REFERENCES users(id),
  FOREIGN KEY (last_reviewed_by) REFERENCES users(id),
  INDEX idx_plan_type (plan_type),
  INDEX idx_status (status),
  INDEX idx_created_by (created_by)
);

-- Plan permissions table for fine-grained access control
CREATE TABLE IF NOT EXISTS business_continuity_plan_permissions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  user_id INT,
  company_id INT,
  permission_level ENUM('read', 'edit') NOT NULL DEFAULT 'read',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES business_continuity_plans(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  UNIQUE KEY unique_user_plan (plan_id, user_id),
  UNIQUE KEY unique_company_plan (plan_id, company_id),
  INDEX idx_plan_id (plan_id),
  INDEX idx_user_id (user_id),
  INDEX idx_company_id (company_id)
);
