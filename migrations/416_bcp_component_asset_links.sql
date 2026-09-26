-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Links continuity-plan components to the canonical asset inventory. Snapshot
-- labels are deliberately limited to non-secret identifiers so an approved
-- plan still explains a historical link after an asset is renamed/archived.
CREATE TABLE IF NOT EXISTS bcp_component_asset_links (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    plan_id INT NOT NULL,
    component_type VARCHAR(40) NOT NULL,
    component_id INT NOT NULL,
    asset_id INT NOT NULL,
    asset_name_snapshot VARCHAR(255) NOT NULL,
    asset_type_snapshot VARCHAR(255) NULL,
    asset_serial_snapshot VARCHAR(255) NULL,
    linked_by_user_id INT NULL,
    linked_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    unlinked_by_user_id INT NULL,
    unlinked_at DATETIME(6) NULL,
    CONSTRAINT fk_bcp_asset_link_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_bcp_asset_link_plan FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
    CONSTRAINT fk_bcp_asset_link_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
    CONSTRAINT fk_bcp_asset_link_linker FOREIGN KEY (linked_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_bcp_asset_link_unlinker FOREIGN KEY (unlinked_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_bcp_asset_link_component (company_id, component_type, component_id, unlinked_at),
    INDEX idx_bcp_asset_link_asset (company_id, asset_id, unlinked_at),
    INDEX idx_bcp_asset_link_plan (plan_id, unlinked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
