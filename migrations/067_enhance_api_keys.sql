ALTER TABLE api_keys
    MODIFY api_key VARCHAR(128) NOT NULL;

ALTER TABLE api_keys
    ADD COLUMN key_prefix VARCHAR(16) NULL AFTER api_key,
    ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN last_used_at DATETIME NULL AFTER created_at;

CREATE INDEX IF NOT EXISTS idx_api_keys_created_at ON api_keys (created_at);
CREATE INDEX IF NOT EXISTS idx_api_keys_expiry_date ON api_keys (expiry_date);
CREATE INDEX IF NOT EXISTS idx_api_keys_last_used_at ON api_keys (last_used_at);
