-- Add new role permission columns for M365 User Mailboxes, M365 Shared Mailboxes,
-- and Chat Access to user_companies and pending_staff_access tables.
-- Also grants these permissions to Owner and Administrator system roles.

-- user_companies: can_view_m365_user_mailboxes
ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_view_m365_user_mailboxes TINYINT(1);

UPDATE user_companies
SET can_view_m365_user_mailboxes = IFNULL(can_view_m365_user_mailboxes, 0)
WHERE can_view_m365_user_mailboxes IS NULL;

ALTER TABLE user_companies
  MODIFY can_view_m365_user_mailboxes TINYINT(1) NOT NULL DEFAULT 0;

-- user_companies: can_view_m365_shared_mailboxes
ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_view_m365_shared_mailboxes TINYINT(1);

UPDATE user_companies
SET can_view_m365_shared_mailboxes = IFNULL(can_view_m365_shared_mailboxes, 0)
WHERE can_view_m365_shared_mailboxes IS NULL;

ALTER TABLE user_companies
  MODIFY can_view_m365_shared_mailboxes TINYINT(1) NOT NULL DEFAULT 0;

-- user_companies: can_access_chat
ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_access_chat TINYINT(1);

UPDATE user_companies
SET can_access_chat = IFNULL(can_access_chat, 0)
WHERE can_access_chat IS NULL;

ALTER TABLE user_companies
  MODIFY can_access_chat TINYINT(1) NOT NULL DEFAULT 0;

-- pending_staff_access: can_view_m365_user_mailboxes
ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_view_m365_user_mailboxes TINYINT(1);

UPDATE pending_staff_access
SET can_view_m365_user_mailboxes = IFNULL(can_view_m365_user_mailboxes, 0)
WHERE can_view_m365_user_mailboxes IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_view_m365_user_mailboxes TINYINT(1) NOT NULL DEFAULT 0;

-- pending_staff_access: can_view_m365_shared_mailboxes
ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_view_m365_shared_mailboxes TINYINT(1);

UPDATE pending_staff_access
SET can_view_m365_shared_mailboxes = IFNULL(can_view_m365_shared_mailboxes, 0)
WHERE can_view_m365_shared_mailboxes IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_view_m365_shared_mailboxes TINYINT(1) NOT NULL DEFAULT 0;

-- pending_staff_access: can_access_chat
ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_access_chat TINYINT(1);

UPDATE pending_staff_access
SET can_access_chat = IFNULL(can_access_chat, 0)
WHERE can_access_chat IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_access_chat TINYINT(1) NOT NULL DEFAULT 0;

-- Grant m365_user_mailboxes.access, m365_shared_mailboxes.access, and chat.access to Owner role
UPDATE roles
SET permissions = JSON_ARRAY_APPEND(
  JSON_ARRAY_APPEND(
    JSON_ARRAY_APPEND(permissions, '$', 'm365_user_mailboxes.access'),
    '$', 'm365_shared_mailboxes.access'
  ),
  '$', 'chat.access'
)
WHERE name = 'Owner' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.manage"')
  AND NOT JSON_CONTAINS(permissions, '"m365_user_mailboxes.access"');

-- Grant m365_user_mailboxes.access, m365_shared_mailboxes.access, and chat.access to Administrator role
UPDATE roles
SET permissions = JSON_ARRAY_APPEND(
  JSON_ARRAY_APPEND(
    JSON_ARRAY_APPEND(permissions, '$', 'm365_user_mailboxes.access'),
    '$', 'm365_shared_mailboxes.access'
  ),
  '$', 'chat.access'
)
WHERE name = 'Administrator' AND is_system = 1
  AND JSON_CONTAINS(permissions, '"company.admin"')
  AND NOT JSON_CONTAINS(permissions, '"m365_user_mailboxes.access"');
