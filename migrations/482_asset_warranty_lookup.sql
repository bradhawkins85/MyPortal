-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 482: asset warranty lookup.
--
-- The warranty lookup service (app.services.warranty_lookup) asks each
-- manufacturer's own free warranty service for an asset's coverage and keeps
-- the start and expiry dates on the asset. warranty_source names where the
-- dates came from ('dell', 'hp', 'lenovo' or 'manual'); integration syncs
-- leave dates from any of these sources alone. warranty_checked_at is the last
-- lookup attempt, warranty_lookup_status its outcome (found, not_found,
-- unsupported, error) and warranty_lookup_message a short plain-English note.

ALTER TABLE assets ADD COLUMN IF NOT EXISTS warranty_start_date DATE NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS warranty_source VARCHAR(32) NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS warranty_checked_at DATETIME NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS warranty_lookup_status VARCHAR(16) NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS warranty_lookup_message VARCHAR(255) NULL;

CREATE INDEX IF NOT EXISTS idx_assets_warranty_checked ON assets (warranty_checked_at);
