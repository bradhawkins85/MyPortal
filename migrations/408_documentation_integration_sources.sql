-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Durable provenance for documentation integrations.  Source rows are kept
-- separately so canonical assets retain human-authored documentation.

CREATE TABLE IF NOT EXISTS asset_source_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    asset_id INT NULL,
    source VARCHAR(32) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    field_ownership_json TEXT NULL,
    last_seen_at DATETIME NULL,
    last_success_at DATETIME NULL,
    last_error VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_asset_source_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_source_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL,
    UNIQUE KEY uq_asset_source_identity (company_id, source, external_id),
    INDEX idx_asset_source_asset (asset_id),
    INDEX idx_asset_source_status (company_id, status)
);

CREATE TABLE IF NOT EXISTS integration_sync_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    source VARCHAR(32) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    records_processed INT NOT NULL DEFAULT 0,
    safe_error VARCHAR(500) NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    CONSTRAINT fk_integration_sync_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    INDEX idx_integration_sync_runs (company_id, source, started_at)
);
