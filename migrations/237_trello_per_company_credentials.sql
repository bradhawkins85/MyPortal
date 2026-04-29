-- Move Trello API credentials to the companies table so each company can have its own key/token
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_api_key VARCHAR(255) NULL DEFAULT NULL;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_token VARCHAR(255) NULL DEFAULT NULL;
