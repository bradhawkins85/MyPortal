-- Add support for email-based ticket watchers
-- This allows watchers to be identified either by user_id or email address

-- Add email column to ticket_watchers
ALTER TABLE ticket_watchers
ADD COLUMN IF NOT EXISTS email VARCHAR(255) NULL AFTER user_id;

-- Make user_id nullable to support email-only watchers
ALTER TABLE ticket_watchers 
MODIFY COLUMN user_id INT NULL;

-- Drop the old unique constraint
-- Ensure foreign keys retain necessary supporting indexes before dropping the composite key
CREATE INDEX IF NOT EXISTS idx_ticket_watchers_ticket_id
    ON ticket_watchers (ticket_id);

CREATE INDEX IF NOT EXISTS idx_ticket_watchers_user_id
    ON ticket_watchers (user_id);

ALTER TABLE ticket_watchers
DROP INDEX IF EXISTS uq_ticket_watchers_ticket_user;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_watchers_ticket_user
    ON ticket_watchers (ticket_id, user_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_watchers_ticket_email
    ON ticket_watchers (ticket_id, email(191));

-- Add check constraint to ensure at least one of user_id or email is set
-- Note: MySQL 8.0.16+ supports CHECK constraints
-- MariaDB does not support "DROP CHECK IF EXISTS" so we need to conditionally
-- drop the constraint using dynamic SQL if it exists.
SET @chk_name := (
    SELECT CONSTRAINT_NAME
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ticket_watchers'
      AND CONSTRAINT_TYPE = 'CHECK'
      AND CONSTRAINT_NAME = 'chk_ticket_watchers_identity'
);

SET @drop_sql := IF(
    @chk_name IS NOT NULL,
    'ALTER TABLE ticket_watchers DROP CONSTRAINT chk_ticket_watchers_identity',
    'SELECT 1'
);

PREPARE stmt FROM @drop_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

ALTER TABLE ticket_watchers
ADD CONSTRAINT chk_ticket_watchers_identity
CHECK (user_id IS NOT NULL OR email IS NOT NULL);
