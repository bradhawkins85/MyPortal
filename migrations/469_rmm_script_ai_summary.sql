-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 469: AI summaries of RMM scripts.
--
-- The Scripts page shows an AI summary of what each script does and what it
-- leaves behind. ai_summary_json holds the summary; ai_summary_sha256 is the
-- content_sha256 of the script it describes, so a summary is regenerated once
-- the script changes in Gitea.

ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS ai_summary_json TEXT NULL;
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS ai_summary_sha256 CHAR(64) NULL;
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS ai_summary_model VARCHAR(255) NULL;
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS ai_summary_updated_at DATETIME NULL;
