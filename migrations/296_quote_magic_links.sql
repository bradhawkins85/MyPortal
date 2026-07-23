-- Add persistent unguessable public download tokens for saved quote PDFs.
ALTER TABLE shop_quotes
  ADD COLUMN IF NOT EXISTS magic_link_token VARCHAR(128) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_shop_quotes_magic_link_token
  ON shop_quotes (magic_link_token);
