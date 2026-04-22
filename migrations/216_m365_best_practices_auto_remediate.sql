-- Microsoft 365 Best Practices – auto-remediation setting
--
-- Adds an auto_remediate column to m365_best_practice_settings so that
-- each check can be individually configured to automatically run remediation
-- immediately after each evaluation (if the check has_remediation support).
--
--   auto_remediate  TINYINT(1) NOT NULL DEFAULT 0
--     0 = manual remediation only (default)
--     1 = automatically remediate after each evaluation if the check fails

ALTER TABLE m365_best_practice_settings
    ADD COLUMN IF NOT EXISTS auto_remediate TINYINT(1) NOT NULL DEFAULT 0;
