ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_manage_assets TINYINT(1),
  ADD COLUMN IF NOT EXISTS can_manage_invoices TINYINT(1);

UPDATE user_companies
SET can_manage_assets = 1, can_manage_invoices = 1
WHERE can_manage_assets IS NULL OR can_manage_invoices IS NULL;

ALTER TABLE user_companies
  MODIFY can_manage_assets TINYINT(1) DEFAULT 0 NOT NULL,
  MODIFY can_manage_invoices TINYINT(1) DEFAULT 0 NOT NULL;
