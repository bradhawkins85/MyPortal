-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Enable automatic RMM agent deployment for all existing companies, including
-- those that previously opted out. This runs once; admins can disable it again
-- afterward. New companies default to enabled in the company repository.
UPDATE companies SET rmm_agent_auto_deploy = 1;
