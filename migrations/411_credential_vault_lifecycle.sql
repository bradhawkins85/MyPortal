-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE credentials ADD COLUMN credential_class VARCHAR(32) NOT NULL DEFAULT 'other';
ALTER TABLE credentials ADD COLUMN owner VARCHAR(191) NULL;
ALTER TABLE credentials ADD COLUMN intended_recipient VARCHAR(191) NULL;
ALTER TABLE credentials ADD COLUMN expires_on DATE NULL;
ALTER TABLE credentials ADD COLUMN review_on DATE NULL;
ALTER TABLE credentials ADD COLUMN last_rotated_at DATETIME(6) NULL;
ALTER TABLE credentials ADD COLUMN archived_at DATETIME(6) NULL;
ALTER TABLE credentials ADD COLUMN revoked_at DATETIME(6) NULL;

CREATE INDEX idx_credentials_company_lifecycle
    ON credentials (company_id, revoked_at, archived_at);
