-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Scripts run as the agent's own account (system) unless a technician
-- chooses the user logged in to the device. The choice is saved with
-- schedules and onboarding steps and captured on each queued run.
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS run_as VARCHAR(16) NOT NULL DEFAULT 'system';
ALTER TABLE rmm_schedules ADD COLUMN IF NOT EXISTS run_as VARCHAR(16) NOT NULL DEFAULT 'system';
ALTER TABLE rmm_onboarding_steps ADD COLUMN IF NOT EXISTS run_as VARCHAR(16) NOT NULL DEFAULT 'system';
ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS run_as_user_supported TINYINT(1) NOT NULL DEFAULT 0;
