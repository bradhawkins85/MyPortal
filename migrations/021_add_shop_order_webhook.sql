ALTER TABLE external_api_settings
  ADD COLUMN IF NOT EXISTS shop_webhook_url VARCHAR(255),
  ADD COLUMN IF NOT EXISTS shop_webhook_api_key VARCHAR(255);
