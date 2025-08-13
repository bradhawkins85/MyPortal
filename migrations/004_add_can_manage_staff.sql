ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_manage_staff TINYINT(1);

UPDATE user_companies
SET can_manage_staff = 1
WHERE can_manage_staff IS NULL;

ALTER TABLE user_companies
  MODIFY can_manage_staff TINYINT(1) DEFAULT 0 NOT NULL;
