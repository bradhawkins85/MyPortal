-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE credential_grants ADD COLUMN workflow_execution_id BIGINT NULL;
ALTER TABLE credential_grants ADD COLUMN workflow_step_identity VARCHAR(255) NULL;
ALTER TABLE credential_grants ADD COLUMN workflow_token_ciphertext TEXT NULL;
CREATE UNIQUE INDEX uq_credential_grant_workflow_step
    ON credential_grants (workflow_execution_id, workflow_step_identity);
