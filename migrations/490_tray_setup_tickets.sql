-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE companies ADD COLUMN tray_setup_tickets_enabled TINYINT(1) NOT NULL DEFAULT 1;
-- Existing devices have already checked in; only future enrolments need setup.
ALTER TABLE tray_devices ADD COLUMN setup_ticket_processed TINYINT(1) NOT NULL DEFAULT 0;
UPDATE tray_devices SET setup_ticket_processed = 1;
