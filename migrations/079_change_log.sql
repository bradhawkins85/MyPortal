CREATE TABLE IF NOT EXISTS change_log (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    guid CHAR(36) NOT NULL,
    occurred_at_utc DATETIME(6) NOT NULL,
    change_type VARCHAR(32) NOT NULL,
    summary TEXT NOT NULL,
    source_file VARCHAR(255) NULL,
    content_hash CHAR(64) NOT NULL,
    created_at_utc DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at_utc DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uniq_change_log_guid (guid),
    UNIQUE KEY uniq_change_log_hash (content_hash),
    KEY idx_change_log_occurred_at (occurred_at_utc),
    KEY idx_change_log_type (change_type)
);
