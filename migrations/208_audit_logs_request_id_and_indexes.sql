-- Link audit_logs rows back to the originating HTTP request via request_id so
-- operators can pivot from a server log entry to the audit row that triggered
-- it (and vice versa), and add indexes that support common admin queries
-- (filtering by user, action and request_id over time).

ALTER TABLE audit_logs
  ADD COLUMN IF NOT EXISTS request_id VARCHAR(64) NULL AFTER metadata;

CREATE INDEX IF NOT EXISTS idx_audit_logs_user_created_at
  ON audit_logs(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_audit_logs_action_created_at
  ON audit_logs(action, created_at);

CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id
  ON audit_logs(request_id);
