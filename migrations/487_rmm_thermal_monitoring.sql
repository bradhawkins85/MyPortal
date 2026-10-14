-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Latest endpoint temperature snapshot, independent of job-poll liveness.
ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS thermal_json TEXT NULL;
