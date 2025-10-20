-- Grant the helpdesk technician permission to default administrative roles.
UPDATE roles
SET
  permissions = CASE
    WHEN JSON_CONTAINS(COALESCE(permissions, JSON_ARRAY()), JSON_QUOTE('helpdesk.technician')) = 1
      THEN permissions
    ELSE JSON_ARRAY_APPEND(COALESCE(permissions, JSON_ARRAY()), '$', 'helpdesk.technician')
  END,
  updated_at = CURRENT_TIMESTAMP
WHERE name IN ('Owner', 'Administrator');
