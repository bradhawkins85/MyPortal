ALTER TABLE scheduled_tasks
  ADD COLUMN IF NOT EXISTS description TEXT NULL,
  ADD COLUMN IF NOT EXISTS max_retries INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS retry_backoff_seconds INT NOT NULL DEFAULT 300,
  ADD COLUMN IF NOT EXISTS last_status VARCHAR(20) NULL,
  ADD COLUMN IF NOT EXISTS last_error TEXT NULL;

CREATE TABLE IF NOT EXISTS scheduled_task_runs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  task_id INT NOT NULL,
  status VARCHAR(20) NOT NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  duration_ms INT NULL,
  details TEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  target_url VARCHAR(500) NOT NULL,
  headers JSON NULL,
  payload JSON NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  response_status INT NULL,
  response_body TEXT NULL,
  attempt_count INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  backoff_seconds INT NOT NULL DEFAULT 300,
  next_attempt_at DATETIME NULL,
  last_error TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhook_event_attempts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  event_id INT NOT NULL,
  attempt_number INT NOT NULL,
  status VARCHAR(20) NOT NULL,
  response_status INT NULL,
  response_body TEXT NULL,
  error_message TEXT NULL,
  attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (event_id) REFERENCES webhook_events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status_next_attempt
  ON webhook_events (status, next_attempt_at);
