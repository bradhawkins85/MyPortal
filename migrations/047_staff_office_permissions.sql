ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS staff_permission TINYINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS can_manage_office_groups TINYINT(1) NOT NULL DEFAULT 0;

UPDATE user_companies
SET staff_permission = IF(can_manage_staff = 1, 3, 0),
    can_manage_office_groups = can_manage_staff
WHERE staff_permission = 0;
