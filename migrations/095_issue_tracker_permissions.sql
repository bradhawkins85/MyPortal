ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_manage_issues TINYINT(1);

UPDATE user_companies
SET
  can_manage_issues = IFNULL(can_manage_issues, 0)
WHERE
  can_manage_issues IS NULL;

ALTER TABLE user_companies
  MODIFY can_manage_issues TINYINT(1) DEFAULT 0 NOT NULL;

ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_manage_issues TINYINT(1);

UPDATE pending_staff_access
SET
  can_manage_issues = IFNULL(can_manage_issues, 0)
WHERE
  can_manage_issues IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_manage_issues TINYINT(1) DEFAULT 0 NOT NULL;
