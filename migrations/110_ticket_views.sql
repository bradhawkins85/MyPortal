-- Add ticket views table for saving user filter and grouping preferences
CREATE TABLE IF NOT EXISTS ticket_views (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(128) NOT NULL,
    description TEXT NULL,
    filters JSON NULL,
    grouping_field VARCHAR(64) NULL,
    sort_field VARCHAR(64) NULL,
    sort_direction VARCHAR(4) NULL,
    is_default TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_ticket_views_user_id (user_id),
    INDEX idx_ticket_views_default (user_id, is_default),
    CONSTRAINT fk_ticket_views_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
