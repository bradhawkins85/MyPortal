-- Migration 201: Direction-aware company workflow policies
-- Allow separate workflow policy rows for onboarding and offboarding per company.
-- Existing rows default to direction='onboarding'.

ALTER TABLE company_onboarding_workflow_policies
    ADD COLUMN IF NOT EXISTS direction VARCHAR(32) NOT NULL DEFAULT 'onboarding' AFTER company_id;

-- Drop the old unique constraint on company_id alone so we can add one on
-- (company_id, direction), which allows one row per direction per company.
DROP INDEX IF EXISTS uq_company_onboarding_workflow_policies_company ON company_onboarding_workflow_policies;

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_workflow_policy_company_dir
    ON company_onboarding_workflow_policies (company_id, direction);
