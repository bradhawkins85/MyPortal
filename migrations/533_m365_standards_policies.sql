-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 533: Microsoft 365 standards policies.
--
-- A policy is a named set of standards (one tenant setting each) that applies
-- to every Microsoft 365 tenant matching its filter rules, such as a licence
-- SKU or a company variable. Each standard runs in report, alert or remediate
-- mode. Companies can be pinned into or out of a policy regardless of rules.

CREATE TABLE IF NOT EXISTS m365_policies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(150) NOT NULL,
  description TEXT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  target_rules TEXT NULL,
  created_by INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS m365_policy_standards (
  id INT AUTO_INCREMENT PRIMARY KEY,
  policy_id INT NOT NULL,
  standard_id VARCHAR(100) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'report',
  settings TEXT NULL,
  CONSTRAINT chk_m365_policy_standard_mode CHECK (mode IN ('report', 'alert', 'remediate')),
  CONSTRAINT fk_m365_policy_standards_policy FOREIGN KEY (policy_id) REFERENCES m365_policies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_policy_standard
  ON m365_policy_standards (policy_id, standard_id);

CREATE TABLE IF NOT EXISTS m365_policy_company_overrides (
  policy_id INT NOT NULL,
  company_id INT NOT NULL,
  action VARCHAR(16) NOT NULL,
  PRIMARY KEY (policy_id, company_id),
  CONSTRAINT chk_m365_policy_override_action CHECK (action IN ('include', 'exclude')),
  CONSTRAINT fk_m365_policy_overrides_policy FOREIGN KEY (policy_id) REFERENCES m365_policies(id) ON DELETE CASCADE,
  CONSTRAINT fk_m365_policy_overrides_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_m365_policy_overrides_company
  ON m365_policy_company_overrides (company_id);
