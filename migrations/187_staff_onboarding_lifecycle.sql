ALTER TABLE staff
  ADD COLUMN IF NOT EXISTS onboarding_status VARCHAR(32) NOT NULL DEFAULT 'requested',
  ADD COLUMN IF NOT EXISTS onboarding_complete TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS onboarding_completed_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ADD COLUMN IF NOT EXISTS updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_staff_company_onboarding_status
  ON staff (company_id, onboarding_status);

CREATE INDEX IF NOT EXISTS idx_staff_company_onboarding_complete
  ON staff (company_id, onboarding_complete);

CREATE INDEX IF NOT EXISTS idx_staff_company_updated_id
  ON staff (company_id, updated_at, id);

CREATE INDEX IF NOT EXISTS idx_staff_company_created_id
  ON staff (company_id, created_at, id);
