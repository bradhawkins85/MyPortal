-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 584: technicians' busy time imported from their Microsoft 365 calendars.
--
-- When scheduling calendar sync is on, the background job reads each
-- technician's calendar and stores only when they are busy (start, end and
-- free/busy state). Event subjects, bodies, attendees and locations are never
-- read or stored: the dispatch board shows these windows as "Unavailable" and
-- booking over them is refused. Rows are replaced for each technician on every
-- import.

CREATE TABLE IF NOT EXISTS technician_busy_times (
    id INT AUTO_INCREMENT PRIMARY KEY,
    technician_user_id INT NOT NULL,
    starts_at DATETIME NOT NULL,
    ends_at DATETIME NOT NULL,
    show_as VARCHAR(20) NOT NULL DEFAULT 'busy',
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_technician_busy_times_user FOREIGN KEY (technician_user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_technician_busy_times_window ON technician_busy_times (technician_user_id, starts_at, ends_at);

ALTER TABLE scheduling_settings ADD COLUMN IF NOT EXISTS busy_synced_at DATETIME NULL;
ALTER TABLE scheduling_settings ADD COLUMN IF NOT EXISTS busy_sync_error VARCHAR(500) NULL;
