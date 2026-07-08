-- Store each M365 mail sync run so admins can investigate past imports/failures.

CREATE TABLE IF NOT EXISTS m365_mail_sync_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  account_id INT NOT NULL,
  status VARCHAR(64) NOT NULL,
  processed INT NOT NULL DEFAULT 0,
  created_count INT NOT NULL DEFAULT 0,
  attached_count INT NOT NULL DEFAULT 0,
  ignored_count INT NOT NULL DEFAULT 0,
  error_count INT NOT NULL DEFAULT 0,
  errors JSON NULL,
  message_actions JSON NULL,
  started_at DATETIME(6) NOT NULL,
  completed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  FOREIGN KEY (account_id) REFERENCES m365_mail_accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_m365_mail_sync_history_account_completed ON m365_mail_sync_history (account_id, completed_at);
CREATE INDEX IF NOT EXISTS idx_m365_mail_sync_history_status ON m365_mail_sync_history (status);
