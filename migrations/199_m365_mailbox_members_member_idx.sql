-- Add index on (company_id, member_upn) to support reverse lookup:
-- which mailboxes is a given member allowed to access?
-- This is used by get_mailboxes_accessible_by_member in the repository.
SET @idx_exists := (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'm365_mailbox_members'
      AND index_name = 'idx_m365mm_company_member'
);
SET @sql := IF(
    @idx_exists = 0,
    'ALTER TABLE m365_mailbox_members ADD KEY idx_m365mm_company_member (company_id, member_upn)',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;
