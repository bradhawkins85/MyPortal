-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Existing scripts and queued runs default to exclusive execution per device.
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS allow_parallel TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS allow_parallel TINYINT(1) NOT NULL DEFAULT 0;
