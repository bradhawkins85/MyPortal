-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 466: company scope for RMM scripts.
--
-- Scripts live in the Gitea repository's Common/ folder (usable for every
-- company, company_id NULL) or in a company's folder under Companies/ (usable
-- only on that company's devices). A script whose company is deleted is never
-- offered to another company, and the next sync deactivates it.

ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS company_id INT NULL;

CREATE INDEX IF NOT EXISTS idx_rmm_scripts_company ON rmm_scripts (company_id, is_active);
