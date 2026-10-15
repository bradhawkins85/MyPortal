-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Run Anyway is opt-in and requires an agent that can bypass execution slots.
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS run_anyway TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS run_anyway TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS run_anyway_supported TINYINT(1) NOT NULL DEFAULT 0;
