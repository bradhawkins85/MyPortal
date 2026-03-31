-- Migration 197: Allow multiple workflow execution records per staff member
-- This enables full execution history tracking so super admins can view
-- each time an automation executed and the result of each step.

-- Drop the unique constraint that prevented more than one execution per staff member.
-- SQLite: this statement may fail silently (SQLite ignores ALTER TABLE errors in migration runner).
ALTER TABLE staff_onboarding_workflow_executions
    DROP INDEX uq_staff_onboarding_workflow_executions_staff;

-- Add a plain index so staff_id lookups remain fast.
ALTER TABLE staff_onboarding_workflow_executions
    ADD INDEX idx_staff_onboarding_workflow_executions_staff (staff_id);
