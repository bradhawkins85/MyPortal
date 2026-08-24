-- The SLA tables were originally created without an explicit collation.  On
-- installations whose database default is utf8mb4_general_ci, their string
-- columns therefore cannot be compared with the utf8mb4_unicode_ci columns on
-- tickets.  The SLA dashboard joins priorities and statuses across those
-- tables, so keep every value participating in those comparisons consistent.
ALTER TABLE sla_template_targets
    MODIFY priority VARCHAR(32)
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL;

ALTER TABLE sla_template_pause_statuses
    MODIFY status VARCHAR(64)
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL;

ALTER TABLE ticket_status_history
    MODIFY status VARCHAR(64)
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL;
