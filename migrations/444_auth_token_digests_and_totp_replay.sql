-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 444: Authentication hardening.
-- Password reset and account verification tokens are now stored as SHA-256
-- digests. token_hashed marks rows written that way; older plaintext rows
-- (token_hashed = 0) keep working until they expire and are only matched by
-- their raw value, so a stored digest can never be replayed as a token.
ALTER TABLE password_tokens ADD COLUMN IF NOT EXISTS token_hashed TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE account_verification_tokens ADD COLUMN IF NOT EXISTS token_hashed TINYINT(1) NOT NULL DEFAULT 0;
