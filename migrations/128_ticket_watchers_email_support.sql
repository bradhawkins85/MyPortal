-- Add support for email-based ticket watchers
-- This allows watchers to be identified either by user_id or email address

-- Add email column to ticket_watchers
ALTER TABLE ticket_watchers 
ADD COLUMN email VARCHAR(255) NULL AFTER user_id;

-- Make user_id nullable to support email-only watchers
ALTER TABLE ticket_watchers 
MODIFY COLUMN user_id INT NULL;

-- Drop the old unique constraint
ALTER TABLE ticket_watchers 
DROP INDEX uq_ticket_watchers_ticket_user;

-- Add new unique constraint that handles both user_id and email
-- This ensures a watcher can't be added twice (either by user_id or email)
ALTER TABLE ticket_watchers 
ADD UNIQUE INDEX uq_ticket_watchers_ticket_user_email (
    ticket_id, 
    user_id, 
    email(191)
);

-- Add index for email lookups
ALTER TABLE ticket_watchers 
ADD INDEX idx_ticket_watchers_email (email(191));

-- Add check constraint to ensure at least one of user_id or email is set
-- Note: MySQL 8.0.16+ supports CHECK constraints
ALTER TABLE ticket_watchers 
ADD CONSTRAINT chk_ticket_watchers_identity 
CHECK (user_id IS NOT NULL OR email IS NOT NULL);
