-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Share the Checks device rule engine with event rules and automation.
ALTER TABLE rmm_schedules ADD COLUMN IF NOT EXISTS target_rules_json LONGTEXT NULL;
ALTER TABLE rmm_onboarding_steps ADD COLUMN IF NOT EXISTS target_rules_json LONGTEXT NULL;
ALTER TABLE rmm_onboarding_steps ADD COLUMN IF NOT EXISTS target_mode VARCHAR(16) NOT NULL DEFAULT 'tags';
ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS target_rules_json LONGTEXT NULL;
