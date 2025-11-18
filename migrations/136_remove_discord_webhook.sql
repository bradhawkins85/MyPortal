-- Remove Discord webhook functionality from shop_settings
-- The table only contained discord_webhook_url and is no longer needed

DROP TABLE IF EXISTS shop_settings;
