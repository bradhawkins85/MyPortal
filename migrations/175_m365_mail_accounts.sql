CREATE TABLE IF NOT EXISTS m365_mail_accounts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  user_principal_name VARCHAR(255) NOT NULL,
  mailbox_type VARCHAR(32) NOT NULL DEFAULT 'user',
  folder VARCHAR(255) NOT NULL DEFAULT 'Inbox',
  process_unread_only TINYINT(1) NOT NULL DEFAULT 1,
  mark_as_read TINYINT(1) NOT NULL DEFAULT 1,
  sync_known_only TINYINT(1) NOT NULL DEFAULT 0,
  schedule_cron VARCHAR(100) NOT NULL,
  filter_query TEXT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  priority SMALLINT NOT NULL DEFAULT 100,
  scheduled_task_id INT NULL,
  last_synced_at DATETIME NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NULL DEFAULT NULL,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (scheduled_task_id) REFERENCES scheduled_tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS m365_mail_account_messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  account_id INT NOT NULL,
  message_uid VARCHAR(512) NOT NULL,
  ticket_id INT NULL,
  status VARCHAR(32) NOT NULL,
  error TEXT NULL,
  processed_at DATETIME NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_m365_mail_account_messages_account_uid (account_id, message_uid),
  FOREIGN KEY (account_id) REFERENCES m365_mail_accounts(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
);
