-- Add compliance_checks permission columns to user_companies and pending_staff_access,
-- and grant new role-based permissions to Owner / Administrator system roles.

-- user_companies: can_view_compliance_checks
ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_view_compliance_checks TINYINT(1);

UPDATE user_companies
SET can_view_compliance_checks = IFNULL(can_view_compliance_checks, 0)
WHERE can_view_compliance_checks IS NULL;

ALTER TABLE user_companies
  MODIFY can_view_compliance_checks TINYINT(1) NOT NULL DEFAULT 0;

-- user_companies: can_manage_compliance_checks
ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_manage_compliance_checks TINYINT(1);

UPDATE user_companies
SET can_manage_compliance_checks = IFNULL(can_manage_compliance_checks, 0)
WHERE can_manage_compliance_checks IS NULL;

ALTER TABLE user_companies
  MODIFY can_manage_compliance_checks TINYINT(1) NOT NULL DEFAULT 0;

-- pending_staff_access: can_view_compliance_checks
ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_view_compliance_checks TINYINT(1);

UPDATE pending_staff_access
SET can_view_compliance_checks = IFNULL(can_view_compliance_checks, 0)
WHERE can_view_compliance_checks IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_view_compliance_checks TINYINT(1) NOT NULL DEFAULT 0;

-- pending_staff_access: can_manage_compliance_checks
ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_manage_compliance_checks TINYINT(1);

UPDATE pending_staff_access
SET can_manage_compliance_checks = IFNULL(can_manage_compliance_checks, 0)
WHERE can_manage_compliance_checks IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_manage_compliance_checks TINYINT(1) NOT NULL DEFAULT 0;

-- Grant compliance_checks.access and compliance_checks.manage to Owner role
UPDATE roles
SET permissions = JSON_ARRAY_APPEND(
  JSON_ARRAY_APPEND(permissions, '$', 'compliance_checks.access'),
  '$', 'compliance_checks.manage'
)
WHERE name = 'Owner' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.manage"')
  AND NOT JSON_CONTAINS(permissions, '"compliance_checks.access"');

-- Grant compliance_checks.access and compliance_checks.manage to Administrator role
UPDATE roles
SET permissions = JSON_ARRAY_APPEND(
  JSON_ARRAY_APPEND(permissions, '$', 'compliance_checks.access'),
  '$', 'compliance_checks.manage'
)
WHERE name = 'Administrator' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.admin"')
  AND NOT JSON_CONTAINS(permissions, '"compliance_checks.access"');
