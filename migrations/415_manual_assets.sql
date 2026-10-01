-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE assets
    ADD COLUMN provenance VARCHAR(20) NOT NULL DEFAULT 'integration',
    ADD COLUMN manual_created_by INT NULL,
    ADD CONSTRAINT fk_assets_manual_creator FOREIGN KEY (manual_created_by) REFERENCES users(id) ON DELETE SET NULL;
