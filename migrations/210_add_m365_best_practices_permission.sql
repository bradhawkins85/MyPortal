-- Add can_view_m365_best_practices permission to user_companies
-- and pending_staff_access tables, plus role permission mapping.

ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_view_m365_best_practices TINYINT(1);

UPDATE user_companies
SET can_view_m365_best_practices = IFNULL(can_view_m365_best_practices, 0)
WHERE can_view_m365_best_practices IS NULL;

ALTER TABLE user_companies
  MODIFY can_view_m365_best_practices TINYINT(1) DEFAULT 0 NOT NULL;

ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_view_m365_best_practices TINYINT(1);

UPDATE pending_staff_access
SET can_view_m365_best_practices = IFNULL(can_view_m365_best_practices, 0)
WHERE can_view_m365_best_practices IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_view_m365_best_practices TINYINT(1) DEFAULT 0 NOT NULL;

-- Grant the new role-based permission to system Owner and Administrator roles
UPDATE roles
SET permissions = JSON_ARRAY_APPEND(permissions, '$', 'm365_best_practices.access')
WHERE name = 'Owner' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.manage"')
  AND NOT JSON_CONTAINS(permissions, '"m365_best_practices.access"');

UPDATE roles
SET permissions = JSON_ARRAY_APPEND(permissions, '$', 'm365_best_practices.access')
WHERE name = 'Administrator' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.admin"')
  AND NOT JSON_CONTAINS(permissions, '"m365_best_practices.access"');
