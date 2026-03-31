CREATE TABLE IF NOT EXISTS stock_feed_price_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(255) NOT NULL,
    dbp DECIMAL(10,4) NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_stock_feed_price_history_sku (sku),
    INDEX idx_stock_feed_price_history_sku_recorded (sku, recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
