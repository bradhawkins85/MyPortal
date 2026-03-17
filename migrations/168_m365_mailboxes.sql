CREATE TABLE IF NOT EXISTS m365_mailboxes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    user_principal_name VARCHAR(320) NOT NULL,
    display_name VARCHAR(255) NOT NULL DEFAULT '',
    mailbox_type VARCHAR(50) NOT NULL DEFAULT 'UserMailbox',
    storage_used_bytes BIGINT NOT NULL DEFAULT 0,
    archive_storage_used_bytes BIGINT NULL,
    has_archive TINYINT(1) NOT NULL DEFAULT 0,
    forwarding_rule_count INT NOT NULL DEFAULT 0,
    synced_at DATETIME NULL,
    UNIQUE KEY uq_m365mb_company_upn (company_id, user_principal_name),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
