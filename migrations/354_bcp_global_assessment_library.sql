-- Global BCP risk and BIA assessment library with per-customer assignments.
CREATE TABLE IF NOT EXISTS bcp_global_risk (
  id INT AUTO_INCREMENT PRIMARY KEY,
  description TEXT NOT NULL,
  likelihood INT NOT NULL,
  impact INT NOT NULL,
  preventative_actions TEXT,
  contingency_plans TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT ck_global_risk_likelihood CHECK (likelihood BETWEEN 1 AND 4),
  CONSTRAINT ck_global_risk_impact CHECK (impact BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bcp_global_bia (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  priority ENUM('High', 'Medium', 'Low'),
  supplier_dependency ENUM('None', 'Sole', 'Major', 'Many'),
  importance INT,
  notes TEXT,
  losses_financial TEXT,
  losses_increased_costs TEXT,
  losses_staffing TEXT,
  losses_product_service TEXT,
  losses_reputation TEXT,
  fines TEXT,
  legal_liability TEXT,
  rto_hours INT,
  losses_comments TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT ck_global_bia_importance CHECK (importance BETWEEN 1 AND 5 OR importance IS NULL),
  CONSTRAINT ck_global_bia_rto CHECK (rto_hours >= 0 OR rto_hours IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bcp_global_risk_assignment (
  global_risk_id INT NOT NULL,
  company_id INT NOT NULL,
  risk_id INT NOT NULL,
  assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (global_risk_id, company_id),
  FOREIGN KEY (global_risk_id) REFERENCES bcp_global_risk(id) ON DELETE CASCADE,
  FOREIGN KEY (risk_id) REFERENCES bcp_risk(id) ON DELETE CASCADE,
  INDEX idx_global_risk_assignment_company (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bcp_global_bia_assignment (
  global_bia_id INT NOT NULL,
  company_id INT NOT NULL,
  critical_activity_id INT NOT NULL,
  assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (global_bia_id, company_id),
  FOREIGN KEY (global_bia_id) REFERENCES bcp_global_bia(id) ON DELETE CASCADE,
  FOREIGN KEY (critical_activity_id) REFERENCES bcp_critical_activity(id) ON DELETE CASCADE,
  INDEX idx_global_bia_assignment_company (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
