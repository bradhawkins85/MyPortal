-- Create the table only if it doesn't already exist to ensure that
-- rerunning the migration doesn't throw an error when the table is
-- present. This makes the migration idempotent and keeps application
-- start-up resilient even if the database was partially migrated.
CREATE TABLE IF NOT EXISTS app_price_options (
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

-- Populate the table with default pricing options.  Using INSERT IGNORE
-- prevents duplicate entry errors if the migration is executed after
-- rows have already been inserted.
INSERT IGNORE INTO app_price_options (app_id, payment_term, contract_term, price)
SELECT
  id,
  'monthly',
  CASE
    WHEN LOWER(contract_term) IN ('monthly', 'month') THEN 'monthly'
    WHEN LOWER(contract_term) IN ('annual', 'year', 'yearly') THEN 'annual'
    ELSE 'annual'
  END,
  default_price
FROM apps;

ALTER TABLE apps
  DROP COLUMN IF EXISTS default_price,
  DROP COLUMN IF EXISTS contract_term;
