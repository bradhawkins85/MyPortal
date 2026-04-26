CREATE TABLE IF NOT EXISTS license_usage_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    license_id INT NOT NULL,
    count INT NOT NULL,
    allocated INT NOT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_license_usage_history_license_id (license_id),
    INDEX idx_license_usage_history_license_recorded (license_id, recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
