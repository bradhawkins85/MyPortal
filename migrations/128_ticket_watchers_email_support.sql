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

-- Add unique constraint for user_id based watchers (where user_id IS NOT NULL)
-- This ensures a user can only watch a ticket once
ALTER TABLE ticket_watchers 
ADD UNIQUE INDEX uq_ticket_watchers_ticket_user (ticket_id, user_id);

-- Add unique constraint for email based watchers (where email IS NOT NULL)
-- This ensures an email can only watch a ticket once
ALTER TABLE ticket_watchers 
ADD UNIQUE INDEX uq_ticket_watchers_ticket_email (ticket_id, email(191));

-- Add check constraint to ensure at least one of user_id or email is set
-- Note: MySQL 8.0.16+ supports CHECK constraints
ALTER TABLE ticket_watchers 
ADD CONSTRAINT chk_ticket_watchers_identity 
CHECK (user_id IS NOT NULL OR email IS NOT NULL);
