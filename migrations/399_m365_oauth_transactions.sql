-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- OAuth callbacks may land on a different web worker from the initiating
-- request. Keep their encrypted, single-use state in shared durable storage.

CREATE TABLE IF NOT EXISTS m365_oauth_transactions (
  transaction_id VARCHAR(64) PRIMARY KEY,
  encrypted_payload TEXT NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  consumed_at DATETIME NULL
);

CREATE INDEX IF NOT EXISTS idx_m365_oauth_transactions_expiry
  ON m365_oauth_transactions (expires_at);
