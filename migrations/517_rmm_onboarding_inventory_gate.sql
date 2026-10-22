-- Automatic onboarding waits for the initial hardware and software snapshot.
-- Existing runs and explicitly requested reruns keep their current behaviour.
ALTER TABLE rmm_onboarding_runs ADD COLUMN IF NOT EXISTS wait_for_inventory TINYINT(1) NOT NULL DEFAULT 0;
