ALTER TABLE audit_logs
  ADD COLUMN entity_type VARCHAR(100) NULL AFTER action,
  ADD COLUMN entity_id INT NULL AFTER entity_type,
  ADD COLUMN metadata JSON NULL AFTER new_value;

CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);
