ALTER TABLE audit_logs
  ADD COLUMN company_id INT NULL AFTER user_id,
  ADD INDEX idx_audit_logs_company_id (company_id);
