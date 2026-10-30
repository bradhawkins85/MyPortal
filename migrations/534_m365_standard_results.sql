-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 534: latest result of each Microsoft 365 standard per tenant.
--
-- One row per company and standard, written by the policy run. status is
-- compliant, drift, remediated, failed or unknown; policy_id and mode record
-- which policy assignment produced it. drift_since marks when the tenant
-- first drifted so alerts fire once per drift rather than on every run.

CREATE TABLE IF NOT EXISTS m365_standard_results (
  company_id INT NOT NULL,
  standard_id VARCHAR(100) NOT NULL,
  policy_id INT NULL,
  mode VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL,
  details TEXT NULL,
  affected TEXT NULL,
  checked_at DATETIME NOT NULL,
  drift_since DATETIME NULL,
  remediated_at DATETIME NULL,
  PRIMARY KEY (company_id, standard_id),
  CONSTRAINT chk_m365_standard_result_status CHECK (status IN ('compliant', 'drift', 'remediated', 'failed', 'unknown')),
  CONSTRAINT fk_m365_standard_results_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
