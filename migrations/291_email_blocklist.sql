CREATE TABLE IF NOT EXISTS email_blocklist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(320) NOT NULL UNIQUE,
    reason TEXT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'manual',
    last_event_type VARCHAR(64) NULL,
    last_event_payload TEXT NULL,
    created_by_user_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_email_blocklist_email ON email_blocklist (email);
CREATE INDEX IF NOT EXISTS idx_email_blocklist_source ON email_blocklist (source);
