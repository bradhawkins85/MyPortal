-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Store per-company Trello OAuth 2.0 (Atlassian 3LO) tokens so a company can
-- connect its Trello account instead of pasting a legacy API key and token.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_oauth_access_token TEXT NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_oauth_refresh_token TEXT NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_oauth_expires_at DATETIME NULL DEFAULT NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_oauth_member VARCHAR(255) NULL DEFAULT NULL;
