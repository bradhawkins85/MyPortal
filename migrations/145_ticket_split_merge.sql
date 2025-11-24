-- Add support for ticket split and merge functionality
-- Migration 145: Add merged_into_ticket_id and split_from_ticket_id columns

-- Add merged_into_ticket_id column to track when a ticket has been merged into another
ALTER TABLE tickets
ADD COLUMN IF NOT EXISTS merged_into_ticket_id INT NULL AFTER external_reference,
ADD INDEX IF NOT EXISTS idx_tickets_merged_into (merged_into_ticket_id);

-- Add split_from_ticket_id column to track when a ticket was split from another
ALTER TABLE tickets
ADD COLUMN IF NOT EXISTS split_from_ticket_id INT NULL AFTER merged_into_ticket_id,
ADD INDEX IF NOT EXISTS idx_tickets_split_from (split_from_ticket_id);

-- Add foreign key constraints
ALTER TABLE tickets
ADD CONSTRAINT fk_tickets_merged_into
    FOREIGN KEY (merged_into_ticket_id) REFERENCES tickets(id) ON DELETE SET NULL;

ALTER TABLE tickets
ADD CONSTRAINT fk_tickets_split_from
    FOREIGN KEY (split_from_ticket_id) REFERENCES tickets(id) ON DELETE SET NULL;
