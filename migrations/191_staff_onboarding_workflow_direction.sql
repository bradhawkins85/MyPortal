-- Migration 191: Direction-aware staff lifecycle workflow executions

ALTER TABLE staff_onboarding_workflow_executions
    ADD COLUMN IF NOT EXISTS direction VARCHAR(32) NOT NULL DEFAULT 'onboarding' AFTER workflow_key;

CREATE INDEX IF NOT EXISTS idx_staff_onboarding_workflow_executions_direction_state
    ON staff_onboarding_workflow_executions (company_id, direction, state);
