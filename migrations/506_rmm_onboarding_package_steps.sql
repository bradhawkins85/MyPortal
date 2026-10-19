-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Onboarding steps can install Chocolatey packages instead of running a
-- script. Such a step has no script and lists its package IDs.
ALTER TABLE rmm_onboarding_steps MODIFY script_id INT NULL;
ALTER TABLE rmm_onboarding_steps ADD COLUMN IF NOT EXISTS package_ids_json LONGTEXT NULL;
