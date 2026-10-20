-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- An onboarding step can skip devices that already existed: skip_existing_before
-- is when the option was turned on, and devices enrolled before then skip the
-- step. NULL runs the step on every device.
ALTER TABLE rmm_onboarding_steps ADD COLUMN IF NOT EXISTS skip_existing_before DATETIME NULL;
