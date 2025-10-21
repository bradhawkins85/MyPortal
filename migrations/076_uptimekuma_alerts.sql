CREATE TABLE IF NOT EXISTS uptimekuma_alerts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  event_uuid VARCHAR(100) NULL,
  monitor_id INT NULL,
  monitor_name VARCHAR(255) NULL,
  monitor_url VARCHAR(500) NULL,
  monitor_type VARCHAR(64) NULL,
  monitor_hostname VARCHAR(255) NULL,
  monitor_port VARCHAR(32) NULL,
  status VARCHAR(32) NOT NULL,
  previous_status VARCHAR(32) NULL,
  importance TINYINT(1) NOT NULL DEFAULT 0,
  alert_type VARCHAR(64) NULL,
  reason VARCHAR(255) NULL,
  message TEXT NULL,
  duration_seconds DOUBLE NULL,
  ping_ms DOUBLE NULL,
  occurred_at DATETIME(6) NULL,
  received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  acknowledged_at DATETIME(6) NULL,
  acknowledged_by INT NULL,
  remote_addr VARCHAR(128) NULL,
  user_agent VARCHAR(255) NULL,
  payload JSON NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_uptimekuma_alerts_user FOREIGN KEY (acknowledged_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE uptimekuma_alerts
  MODIFY payload JSON NOT NULL DEFAULT (JSON_OBJECT());

CREATE INDEX IF NOT EXISTS idx_uptimekuma_status
  ON uptimekuma_alerts (status);

CREATE INDEX IF NOT EXISTS idx_uptimekuma_monitor
  ON uptimekuma_alerts (monitor_id);

CREATE INDEX IF NOT EXISTS idx_uptimekuma_received_at
  ON uptimekuma_alerts (received_at);
