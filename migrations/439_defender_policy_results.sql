-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 439: The tray agent now applies Defender exclusions and scheduled
-- scans, skipping any change Tamper Protection could block. Store the
-- per-setting outcome so the portal can flag endpoints where the policy is not
-- in effect.
ALTER TABLE defender_device_status ADD COLUMN IF NOT EXISTS policy_status VARCHAR(32) NULL;
ALTER TABLE defender_device_status ADD COLUMN IF NOT EXISTS policy_evaluated_at DATETIME NULL;
ALTER TABLE defender_device_status ADD COLUMN IF NOT EXISTS policy_result_json JSON NULL;
