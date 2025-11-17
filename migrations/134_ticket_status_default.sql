-- Add is_default column to ticket_statuses table
ALTER TABLE ticket_statuses 
ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0 AFTER public_status;

-- Add index for is_default column
CREATE INDEX idx_ticket_statuses_default ON ticket_statuses (is_default);

-- Set the first status (usually 'open') as the default
UPDATE ticket_statuses 
SET is_default = 1 
WHERE tech_status = 'open' 
LIMIT 1;

-- If 'open' doesn't exist, set the first status by ID as default
UPDATE ticket_statuses t1
INNER JOIN (
    SELECT MIN(id) as min_id 
    FROM ticket_statuses
) t2
SET t1.is_default = 1
WHERE t1.id = t2.min_id
AND NOT EXISTS (
    SELECT 1 FROM ticket_statuses WHERE is_default = 1
);
