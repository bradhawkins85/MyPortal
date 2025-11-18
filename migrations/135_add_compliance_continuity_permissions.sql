-- Add compliance.access and continuity.access permissions to roles
-- This migration adds two new role-based permissions for controlling access to Compliance and Continuity sections

-- Update Owner role to include new permissions
UPDATE roles 
SET permissions = JSON_ARRAY_APPEND(
  JSON_ARRAY_APPEND(permissions, '$', 'compliance.access'),
  '$', 'continuity.access'
)
WHERE name = 'Owner' AND is_system = 1 
  AND JSON_CONTAINS(permissions, '"company.manage"')
  AND NOT JSON_CONTAINS(permissions, '"compliance.access"');

-- Update Administrator role to include new permissions
UPDATE roles 
SET permissions = JSON_ARRAY_APPEND(
  JSON_ARRAY_APPEND(permissions, '$', 'compliance.access'),
  '$', 'continuity.access'
)
WHERE name = 'Administrator' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.admin"')
  AND NOT JSON_CONTAINS(permissions, '"compliance.access"');
