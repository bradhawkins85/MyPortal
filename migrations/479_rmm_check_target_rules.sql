-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Existing checks retain their company/device scope. NULL means all devices
-- within that scope; a tree combines nested AND/OR inventory conditions.
ALTER TABLE rmm_checks ADD COLUMN IF NOT EXISTS target_rules_json LONGTEXT NULL;
