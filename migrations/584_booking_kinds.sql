-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 584: booking kinds for technician appointments.
--
-- booking_kinds replaces the fixed list of appointment kinds (onsite visit,
-- remote session, phone call, meeting) so admins can add, rename and remove
-- them from the schedule page and choose the kind new bookings start with.
-- appointments.kind keeps holding the kind's key. A kind that appointments
-- still use is archived rather than deleted, so their label stays readable.

CREATE TABLE IF NOT EXISTS booking_kinds (
    id INT AUTO_INCREMENT PRIMARY KEY,
    kind_key VARCHAR(16) NOT NULL,
    label VARCHAR(80) NOT NULL,
    tone VARCHAR(16) NOT NULL DEFAULT 'accent',
    sort_order INT NOT NULL DEFAULT 0,
    is_default TINYINT(1) NOT NULL DEFAULT 0,
    is_archived TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_booking_kinds_key UNIQUE (kind_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO booking_kinds (kind_key, label, tone, sort_order, is_default) VALUES ('onsite', 'Onsite visit', 'warning', 10, 1);
INSERT IGNORE INTO booking_kinds (kind_key, label, tone, sort_order, is_default) VALUES ('remote', 'Remote session', 'accent', 20, 0);
INSERT IGNORE INTO booking_kinds (kind_key, label, tone, sort_order, is_default) VALUES ('phone', 'Phone call', 'success', 30, 0);
INSERT IGNORE INTO booking_kinds (kind_key, label, tone, sort_order, is_default) VALUES ('meeting', 'Meeting', 'neutral', 40, 0);
