-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 582: technician scheduling and dispatch.
--
-- appointments books a technician for a ticket at a time (stored in UTC):
-- an onsite visit, remote session, phone call or meeting. When calendar sync
-- is set up, each appointment is also written to the technician's Microsoft
-- 365 calendar; m365_event_id, sync_status and sync_error record how that
-- went so failed writes can be retried. scheduling_settings holds the single
-- row of settings: which company's Microsoft 365 connection holds the
-- technicians' mailboxes, the board's working hours, time zone and the
-- default appointment length.

CREATE TABLE IF NOT EXISTS appointments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    company_id INT NULL,
    technician_user_id INT NOT NULL,
    kind VARCHAR(16) NOT NULL DEFAULT 'onsite',
    starts_at DATETIME NOT NULL,
    ends_at DATETIME NOT NULL,
    location VARCHAR(500) NULL,
    notes TEXT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'scheduled',
    m365_event_id VARCHAR(512) NULL,
    m365_mailbox VARCHAR(255) NULL,
    sync_status VARCHAR(16) NOT NULL DEFAULT 'off',
    sync_error VARCHAR(500) NULL,
    sync_attempts INT NOT NULL DEFAULT 0,
    created_by_user_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_appointments_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_appointments_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
    CONSTRAINT fk_appointments_technician FOREIGN KEY (technician_user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_appointments_technician ON appointments (technician_user_id, starts_at);
CREATE INDEX idx_appointments_ticket ON appointments (ticket_id);
CREATE INDEX idx_appointments_window ON appointments (starts_at, ends_at);
CREATE INDEX idx_appointments_sync ON appointments (sync_status);

CREATE TABLE IF NOT EXISTS scheduling_settings (
    id INT NOT NULL PRIMARY KEY,
    m365_company_id INT NULL,
    timezone VARCHAR(64) NULL,
    day_start_hour INT NOT NULL DEFAULT 7,
    day_end_hour INT NOT NULL DEFAULT 19,
    default_duration_minutes INT NOT NULL DEFAULT 60,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_scheduling_settings_company FOREIGN KEY (m365_company_id) REFERENCES companies(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO scheduling_settings (id) VALUES (1);
