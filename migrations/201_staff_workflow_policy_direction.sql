-- Migration 201: Direction-aware company workflow policies
-- Allow separate workflow policy rows for onboarding and offboarding per company.
-- Existing rows default to direction='onboarding'.

ALTER TABLE company_onboarding_workflow_policies
    ADD COLUMN IF NOT EXISTS direction VARCHAR(32) NOT NULL DEFAULT 'onboarding' AFTER company_id;

-- Drop the old unique constraint on company_id alone so we can add one on
-- (company_id, direction), which allows one row per direction per company.
-- The FK constraint must be dropped first because MySQL will not drop an index
-- that is required by a foreign key constraint (error 1553).
ALTER TABLE company_onboarding_workflow_policies
    DROP FOREIGN KEY IF EXISTS fk_company_onboarding_workflow_policies_company;

DROP INDEX IF EXISTS uq_company_onboarding_workflow_policies_company ON company_onboarding_workflow_policies;

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_workflow_policy_company_dir
    ON company_onboarding_workflow_policies (company_id, direction);

-- Re-add the foreign key constraint using the new composite index.
ALTER TABLE company_onboarding_workflow_policies
    ADD CONSTRAINT fk_company_onboarding_workflow_policies_company
        FOREIGN KEY IF NOT EXISTS (company_id) REFERENCES companies(id) ON DELETE CASCADE;
