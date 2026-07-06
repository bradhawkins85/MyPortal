ALTER TABLE ticket_statuses
ADD COLUMN IF NOT EXISTS hide_from_technicians TINYINT(1) NOT NULL DEFAULT 0 AFTER is_default;

CREATE INDEX IF NOT EXISTS idx_ticket_statuses_hide_from_technicians ON ticket_statuses (hide_from_technicians);
