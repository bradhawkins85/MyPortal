CREATE TABLE IF NOT EXISTS imap_accounts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NULL,
  name VARCHAR(255) NOT NULL,
  host VARCHAR(255) NOT NULL,
  port SMALLINT NOT NULL DEFAULT 993,
  username VARCHAR(255) NOT NULL,
  password_encrypted TEXT NOT NULL,
  folder VARCHAR(255) NOT NULL DEFAULT 'INBOX',
  process_unread_only TINYINT(1) NOT NULL DEFAULT 1,
  mark_as_read TINYINT(1) NOT NULL DEFAULT 1,
  schedule_cron VARCHAR(100) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  scheduled_task_id INT NULL,
  last_synced_at DATETIME NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NULL DEFAULT NULL,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
  FOREIGN KEY (scheduled_task_id) REFERENCES scheduled_tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS imap_account_messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  account_id INT NOT NULL,
  message_uid VARCHAR(255) NOT NULL,
  ticket_id INT NULL,
  status VARCHAR(32) NOT NULL,
  error TEXT NULL,
  processed_at DATETIME NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_imap_account_messages_account_uid (account_id, message_uid),
  FOREIGN KEY (account_id) REFERENCES imap_accounts(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
);
