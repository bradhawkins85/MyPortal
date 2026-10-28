-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Each script chooses whether it runs as System or the logged-in user by default; existing scripts keep System.
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS default_run_as VARCHAR(16) NOT NULL DEFAULT 'system';
