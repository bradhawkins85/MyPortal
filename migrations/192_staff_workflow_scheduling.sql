-- Migration 192: Persist normalized workflow schedule metadata

ALTER TABLE staff_onboarding_workflow_executions
    ADD COLUMN IF NOT EXISTS scheduled_for_utc DATETIME NULL AFTER requested_at,
    ADD COLUMN IF NOT EXISTS requested_timezone VARCHAR(64) NULL AFTER scheduled_for_utc;
