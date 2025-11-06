CREATE TABLE IF NOT EXISTS tag_exclusions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tag_slug VARCHAR(48) NOT NULL UNIQUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    created_by INT NULL,
    INDEX idx_tag_slug (tag_slug),
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);
