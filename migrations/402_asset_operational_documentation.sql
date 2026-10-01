-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Human-maintained operational documentation lives on the canonical asset row.
-- Synchronisers deliberately do not write these columns.
ALTER TABLE assets
    ADD COLUMN owner VARCHAR(255) NULL,
    ADD COLUMN support_contact VARCHAR(255) NULL,
    ADD COLUMN criticality VARCHAR(20) NULL,
    ADD COLUMN location VARCHAR(255) NULL,
    ADD COLUMN operational_notes TEXT NULL,
    ADD COLUMN review_status VARCHAR(20) NOT NULL DEFAULT 'not_reviewed',
    ADD COLUMN reviewed_at DATETIME NULL,
    ADD COLUMN external_references_json TEXT NULL,
    ADD COLUMN archived_at DATETIME NULL;

CREATE TABLE IF NOT EXISTS asset_type_required_fields (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(255) NOT NULL,
    field_key VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_asset_type_required_field (asset_type, field_key)
);
