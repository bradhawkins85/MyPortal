-- Migration 204: Allow multiple workflow policies per direction per company
-- Adds delay_type (scheduled/immediate), workflow_name for display, and sort_order.
-- Removes the unique constraint on (company_id, direction) so multiple workflows
-- can be configured per direction, with uniqueness enforced on (company_id, direction, workflow_key).

-- Add delay_type: 'scheduled' runs at the configured time, 'immediate' runs right away on approval
ALTER TABLE company_onboarding_workflow_policies
    ADD COLUMN IF NOT EXISTS delay_type VARCHAR(20) NOT NULL DEFAULT 'scheduled' AFTER direction;

-- Add human-readable display name for each workflow
ALTER TABLE company_onboarding_workflow_policies
    ADD COLUMN IF NOT EXISTS workflow_name VARCHAR(255) NULL AFTER workflow_key;

-- Add sort_order for controlling display order
ALTER TABLE company_onboarding_workflow_policies
    ADD COLUMN IF NOT EXISTS sort_order INT NOT NULL DEFAULT 0 AFTER workflow_name;

-- Drop the old unique constraint on (company_id, direction) and FK so we can
-- create the new composite unique key on (company_id, direction, workflow_key).
ALTER TABLE company_onboarding_workflow_policies
    DROP FOREIGN KEY IF EXISTS fk_company_onboarding_workflow_policies_company;

DROP INDEX IF EXISTS uq_company_workflow_policy_company_dir ON company_onboarding_workflow_policies;

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_workflow_policy_company_dir_key
    ON company_onboarding_workflow_policies (company_id, direction, workflow_key);

-- Re-add the foreign key constraint.
ALTER TABLE company_onboarding_workflow_policies
    ADD CONSTRAINT fk_company_onboarding_workflow_policies_company
        FOREIGN KEY IF NOT EXISTS (company_id) REFERENCES companies(id) ON DELETE CASCADE;
