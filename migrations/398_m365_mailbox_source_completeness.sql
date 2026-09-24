-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Unknown source values remain NULL instead of being asserted as zero.

ALTER TABLE m365_mailboxes MODIFY COLUMN storage_used_bytes BIGINT NULL;
ALTER TABLE m365_mailboxes MODIFY COLUMN has_archive TINYINT(1) NULL;
ALTER TABLE m365_mailboxes MODIFY COLUMN forwarding_rule_count INT NULL;

CREATE TABLE IF NOT EXISTS m365_mailbox_sync_state (
    company_id INT NOT NULL,
    category VARCHAR(40) NOT NULL,
    is_complete TINYINT(1) NOT NULL DEFAULT 0,
    last_attempt_at DATETIME NOT NULL,
    last_success_at DATETIME NULL,
    PRIMARY KEY (company_id, category),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
