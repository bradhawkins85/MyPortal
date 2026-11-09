-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Lets techs run a script later: a queued run with scheduled_for (UTC) is not
-- handed to the device until that time. NULL means run as soon as possible.
ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS scheduled_for DATETIME NULL;
