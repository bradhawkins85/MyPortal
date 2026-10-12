-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Script file associations survive Gitea sync. Older agents cannot run scripts
-- that require files until they advertise support on enrolment or check-in.
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS files_json TEXT NULL;
ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS script_files_supported TINYINT(1) NOT NULL DEFAULT 0;
