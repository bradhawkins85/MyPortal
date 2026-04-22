-- Microsoft 365 Best Practices – remediation tracking
--
-- Adds two optional columns to the m365_best_practice_results table so that
-- automated remediation attempts can be recorded alongside the check result:
--
--   remediation_status  VARCHAR(20) – null (not attempted), 'success', or 'failed'
--   remediated_at       DATETIME    – when the remediation was last attempted

ALTER TABLE m365_best_practice_results
    ADD COLUMN IF NOT EXISTS remediation_status VARCHAR(20) NULL,
    ADD COLUMN IF NOT EXISTS remediated_at DATETIME NULL;
