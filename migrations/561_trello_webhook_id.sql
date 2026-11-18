-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Remember the Trello webhook id per company. Trello cannot list OAuth 2.0
-- webhooks, so the stored id is the only way to check or reactivate one.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_webhook_id VARCHAR(64) NULL DEFAULT NULL;
