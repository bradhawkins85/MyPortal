-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS asset_photos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_id INT NOT NULL,
    company_id INT NOT NULL,
    storage_name VARCHAR(255) NOT NULL,
    thumbnail_name VARCHAR(255) NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    size_bytes INT NOT NULL,
    caption VARCHAR(500) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    customer_visible BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key VARCHAR(64) NOT NULL,
    uploaded_by INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_asset_photos_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_photos_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_photos_user FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT uq_asset_photos_retry UNIQUE (company_id, asset_id, idempotency_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_asset_photos_gallery ON asset_photos (company_id, asset_id, sort_order, id);
