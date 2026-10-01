-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Explicitly excludes selected conversation entries from resolution generation.

ALTER TABLE ticket_replies ADD COLUMN IF NOT EXISTS is_not_resolution_step TINYINT(1) NOT NULL DEFAULT 0;
