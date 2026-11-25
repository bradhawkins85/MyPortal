-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Onboarding steps can belong to every company: a step with no company runs
-- (before each company's own steps) on every matching newly enrolled device.
ALTER TABLE rmm_onboarding_steps MODIFY company_id INT NULL;
