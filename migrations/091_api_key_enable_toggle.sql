ALTER TABLE api_keys
    ADD COLUMN is_enabled BOOLEAN NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_api_keys_is_enabled ON api_keys (is_enabled);
