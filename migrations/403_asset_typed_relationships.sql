-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Company ownership is repeated here intentionally: it makes every lookup
-- tenant-scoped and prevents a relationship from becoming a cross-company
-- information channel.
CREATE TABLE IF NOT EXISTS asset_relationships (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    source_asset_id INT NOT NULL,
    target_type VARCHAR(40) NOT NULL,
    target_id BIGINT NOT NULL,
    relationship_type VARCHAR(40) NOT NULL,
    created_by INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_asset_relationship_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_relationship_source FOREIGN KEY (source_asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_relationship_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE KEY uq_asset_typed_relationship (company_id, source_asset_id, target_type, target_id, relationship_type),
    INDEX idx_asset_relationship_target (company_id, target_type, target_id),
    INDEX idx_asset_relationship_source (company_id, source_asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
