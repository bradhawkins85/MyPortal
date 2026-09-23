-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Configurable lifecycle policies for Webhook Monitor history.

CREATE TABLE IF NOT EXISTS webhook_deletion_rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(150) NOT NULL,
  execution_type VARCHAR(20) NOT NULL,
  cron_expression VARCHAR(100) NULL,
  conditions JSON NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  next_run_at DATETIME NULL,
  last_run_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_webhook_deletion_rules_due
  ON webhook_deletion_rules (enabled, execution_type, next_run_at);

CREATE TABLE IF NOT EXISTS webhook_retention_settings (
  id INT PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  retention_days INT NOT NULL DEFAULT 30,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
