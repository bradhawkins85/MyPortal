CREATE TABLE IF NOT EXISTS apps (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(255) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  default_price DECIMAL(10,2) NOT NULL,
  contract_term VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS company_app_prices (
  company_id INT NOT NULL,
  app_id INT NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (company_id, app_id),
  FOREIGN KEY (company_id) REFERENCES companies(id),
  FOREIGN KEY (app_id) REFERENCES apps(id)
);

ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_order_licenses TINYINT(1);

UPDATE user_companies
SET can_order_licenses = 1
WHERE can_order_licenses IS NULL;

ALTER TABLE user_companies
  MODIFY can_order_licenses TINYINT(1) DEFAULT 0 NOT NULL;

ALTER TABLE external_api_settings
  ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(255),
  ADD COLUMN IF NOT EXISTS webhook_api_key VARCHAR(255);
