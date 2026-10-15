-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Existing checks keep their tickets open on recovery unless explicitly enabled.
ALTER TABLE rmm_checks ADD COLUMN IF NOT EXISTS auto_close_when_resolved TINYINT(1) NOT NULL DEFAULT 0;
