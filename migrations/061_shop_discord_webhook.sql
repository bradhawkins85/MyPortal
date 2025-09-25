CREATE TABLE IF NOT EXISTS shop_settings (
  id INT PRIMARY KEY,
  discord_webhook_url VARCHAR(500) NULL
);

INSERT INTO shop_settings (id, discord_webhook_url)
VALUES (1, NULL)
ON DUPLICATE KEY UPDATE discord_webhook_url = discord_webhook_url;
