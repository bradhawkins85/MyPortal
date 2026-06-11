-- Add quote access permission to pending staff assignments so queued access
-- can preserve the same company permissions as active user assignments.

ALTER TABLE pending_staff_access
  ADD COLUMN IF NOT EXISTS can_access_quotes TINYINT(1);

UPDATE pending_staff_access
SET can_access_quotes = IFNULL(can_access_quotes, 0)
WHERE can_access_quotes IS NULL;

ALTER TABLE pending_staff_access
  MODIFY can_access_quotes TINYINT(1) NOT NULL DEFAULT 0;
