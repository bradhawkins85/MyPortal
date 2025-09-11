CREATE TABLE app_price_options (
  id INT AUTO_INCREMENT PRIMARY KEY,
  app_id INT NOT NULL,
  payment_term ENUM('monthly','annual') NOT NULL,
  contract_term ENUM('monthly','annual') NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  UNIQUE KEY uniq_app_term (app_id, payment_term, contract_term),
  FOREIGN KEY (app_id) REFERENCES apps(id)
);

ALTER TABLE company_app_prices
  ADD COLUMN IF NOT EXISTS payment_term ENUM('monthly','annual') NOT NULL DEFAULT 'monthly',
  ADD COLUMN IF NOT EXISTS contract_term ENUM('monthly','annual') NOT NULL DEFAULT 'monthly';

INSERT INTO app_price_options (app_id, payment_term, contract_term, price)
SELECT id, 'monthly', contract_term, default_price FROM apps;

ALTER TABLE apps
  DROP COLUMN IF EXISTS default_price,
  DROP COLUMN IF EXISTS contract_term;
