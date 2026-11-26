-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 577: rename and remove printers from the asset page's Printers
-- section. A technician's request runs as a built-in script through the RMM
-- script runner; the printer row records the requested action, the new name
-- for a rename and the run that carries it, so the page can show the change as
-- pending until the device reports back.

ALTER TABLE rmm_printers ADD COLUMN IF NOT EXISTS pending_action VARCHAR(16) NULL;
ALTER TABLE rmm_printers ADD COLUMN IF NOT EXISTS pending_name VARCHAR(255) NULL;
ALTER TABLE rmm_printers ADD COLUMN IF NOT EXISTS pending_run_id INT NULL;
