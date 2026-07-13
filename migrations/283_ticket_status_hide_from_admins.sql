ALTER TABLE ticket_statuses
ADD COLUMN IF NOT EXISTS hide_from_admins TINYINT(1) NOT NULL DEFAULT 0 AFTER hide_from_technicians;

CREATE INDEX IF NOT EXISTS idx_ticket_statuses_hide_from_admins ON ticket_statuses (hide_from_admins);
