CREATE TABLE IF NOT EXISTS m365_mailbox_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    mailbox_email VARCHAR(320) NOT NULL,
    member_upn VARCHAR(320) NOT NULL,
    member_display_name VARCHAR(255) NOT NULL DEFAULT '',
    synced_at DATETIME NULL,
    UNIQUE KEY uq_m365mm_company_mailbox_member (company_id, mailbox_email, member_upn),
    KEY idx_m365mm_company_mailbox (company_id, mailbox_email),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
