ALTER TABLE staff
  ADD COLUMN IF NOT EXISTS approval_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS requested_by_user_id INT NULL,
  ADD COLUMN IF NOT EXISTS requested_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS approved_by_user_id INT NULL,
  ADD COLUMN IF NOT EXISTS approved_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS request_notes TEXT NULL,
  ADD COLUMN IF NOT EXISTS approval_notes TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_staff_company_approval_status
  ON staff (company_id, approval_status);

CREATE INDEX IF NOT EXISTS idx_staff_requested_by
  ON staff (requested_by_user_id);

CREATE INDEX IF NOT EXISTS idx_staff_approved_by
  ON staff (approved_by_user_id);
