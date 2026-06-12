ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS requester_staff_id INT NULL AFTER requester_id;

CREATE INDEX IF NOT EXISTS idx_tickets_requester_staff_id
  ON tickets (requester_staff_id);
